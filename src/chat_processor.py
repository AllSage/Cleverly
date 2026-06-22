# src/chat_processor.py
import logging
import math
import re
import time
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple
from src.chat_helpers import extract_urls
from src.youtube_handler import is_youtube_url
from src.search import comprehensive_web_search, fetch_webpage_content
from src.prompt_security import UNTRUSTED_CONTEXT_POLICY, untrusted_context_message

logger = logging.getLogger(__name__)

# ── Stopwords & tokenizer ──

_STOPWORDS = frozenset(
    "a an the is am are was were be been being have has had do does did "
    "will would shall should can could may might must need ought dare "
    "i me my mine we us our ours you your yours he him his she her hers "
    "it its they them their theirs this that these those "
    "and but or nor not no so if then else than too also very "
    "in on at to for of by with from up out about into over after "
    "what when where which who whom how why all each every some any "
    "just very really actually like well also still already even "
    "oh ok okay yes yeah hey hi hello thanks thank please sorry "
    "much more most own other another such only same here there "
    "because while during before until since through between both "
    "few many several some none nothing something anything everything "
    "get got make made go going went been come came take took "
    "know think want let say tell give see look find way thing "
    "don doesn didn won wouldn couldn shouldn wasn weren isn aren haven hasn "
    "don't doesn't didn't won't wouldn't couldn't shouldn't "
    "it's i'm i've i'll i'd you're you've you'll he's she's we're we've they're they've "
    "that's there's here's what's who's how's let's can't".split()
)

def _content_tokens(text: str) -> list:
    """Extract meaningful content words: no stopwords, min 3 chars, lowercase."""
    words = re.findall(r'[a-z0-9]+(?:[-_][a-z0-9]+)*', text.lower())
    return [w for w in words if len(w) >= 3 and w not in _STOPWORDS]


def _local_document_search_query(message: str) -> str:
    """Return the requested local-document query, or empty when not explicit."""
    text = str(message or "").strip()
    if not re.search(r"\b(search|find|lookup|look\s+up)\b", text, re.I):
        return ""
    if not re.search(r"\b(local\s+)?(documents?|docs?|files?|library)\b", text, re.I):
        return ""
    query = re.sub(r"^cleverly[,\s]+", "", text, flags=re.I).strip()
    query = re.sub(r"\b(search|find|look\s+up|lookup)\b", "", query, count=1, flags=re.I).strip()
    query = re.sub(r"\b(my\s+)?(local\s+)?(documents?|docs?|files?|library)\b", "", query, flags=re.I).strip()
    query = re.sub(r"^(for|about|on|in|for:)\b", "", query, flags=re.I).strip(" :.-")
    return re.sub(r"\s+", " ", query).strip()


def _rag_search(rag_manager: Any, query: str, *, k: int, owner: Optional[str]) -> list[Dict[str, Any]]:
    """Call old and new RAG search signatures without losing owner support."""
    try:
        return list(rag_manager.search(query, k=k, owner=owner) or [])
    except TypeError as exc:
        if "owner" not in str(exc):
            raise
        return list(rag_manager.search(query, k=k) or [])


def _metadata_filename(metadata: Dict[str, Any]) -> str:
    source = metadata.get("source") or ""
    return metadata.get("filename") or (source.rsplit("/", 1)[-1] if source else "") or "unknown"


def _keyword_document_results(personal_docs_manager: Any, query: str, k: int = 5) -> list[Dict[str, Any]]:
    """Search the in-memory personal-doc index when vector search has no hit."""
    try:
        from src.personal_docs import tokenize
    except Exception:
        return []
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    scored: list[tuple[int, Dict[str, Any], int, str]] = []
    for file_item in getattr(personal_docs_manager, "index", []) or []:
        if not isinstance(file_item, dict):
            continue
        for chunk_idx, chunk in enumerate(file_item.get("chunks") or []):
            score = len(query_tokens & tokenize(chunk))
            if score > 0:
                scored.append((score, file_item, chunk_idx, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    results: list[Dict[str, Any]] = []
    for score, file_item, chunk_idx, chunk in scored[:k]:
        source = file_item.get("path") or ""
        results.append({
            "similarity": min(0.8, max(0.05, score / max(len(query_tokens), 1))),
            "metadata": {
                "filename": file_item.get("name") or _metadata_filename({"source": source}),
                "source": source,
                "chunk_id": chunk_idx,
                "search_type": "keyword",
            },
            "document": chunk,
        })
    return results


class ChatProcessor:
    def __init__(self, memory_manager, personal_docs_manager, memory_vector=None, skills_manager=None):
        self.memory_manager = memory_manager
        self.personal_docs_manager = personal_docs_manager
        self.memory_vector = memory_vector
        self.skills_manager = skills_manager

    # Minimum similarity score for RAG results to be injected
    RAG_SIMILARITY_THRESHOLD = 0.35

    def _hybrid_retrieve(self, message: str, mem_entries: list, k: int = 5) -> list:
        """Retrieve memories relevant to the message.

        Uses BM25-style keyword scoring + optional vector similarity.
        Recency is a tiebreaker only, never the primary signal.
        """
        if not mem_entries or not message.strip():
            return []

        now = time.time()
        query_tokens = _content_tokens(message)

        # If the query has no meaningful tokens, skip keyword retrieval entirely
        if not query_tokens:
            # Fall back to vector-only if available
            if not (self.memory_vector and self.memory_vector.healthy):
                return []

        # ── Build IDF from the memory corpus ──
        N = len(mem_entries)
        doc_freq = Counter()  # token -> how many memories contain it
        mem_token_cache = {}  # mem_id -> set of content tokens
        for mem in mem_entries:
            toks = set(_content_tokens(mem["text"]))
            mem_token_cache[mem["id"]] = toks
            for t in toks:
                doc_freq[t] += 1

        def _bm25_score(query_toks, mem_id):
            """BM25-inspired score between query and a memory."""
            mem_toks = mem_token_cache.get(mem_id, set())
            if not mem_toks or not query_toks:
                return 0.0
            score = 0.0
            mem_len = len(mem_toks)
            avg_len = max(sum(len(v) for v in mem_token_cache.values()) / N, 1)
            k1, b = 1.5, 0.75
            for qt in query_toks:
                if qt not in mem_toks:
                    continue
                df = doc_freq.get(qt, 0)
                idf = math.log((N - df + 0.5) / (df + 0.5) + 1)
                tf = 1  # binary presence (memory entries are short)
                tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * mem_len / avg_len))
                score += idf * tf_norm
            return score

        # ── Score all candidates ──
        has_vector = self.memory_vector and self.memory_vector.healthy
        vector_scores = {}

        if has_vector:
            results = self.memory_vector.search(message, k=min(k * 3, 20))
            mem_by_id = {m["id"]: m for m in mem_entries}
            for r in results:
                if r["memory_id"] in mem_by_id:
                    vector_scores[r["memory_id"]] = max(r["score"], 0.0)

        scored = []
        for mem in mem_entries:
            mid = mem["id"]
            vs = vector_scores.get(mid, 0.0)
            kw = _bm25_score(query_tokens, mid)

            # Normalize BM25 to roughly 0-1 range (cap at a reasonable max)
            kw_norm = min(kw / 6.0, 1.0) if kw > 0 else 0.0

            # Category-aware boost for identity/contact queries
            category = mem.get("category", "fact")
            msg_lower = message.lower()
            mem_lower = mem["text"].lower()
            cat_boost = 1.0
            if any(w in msg_lower for w in ["name", "who am i", "my name"]):
                if category == "identity" or any(w in mem_lower for w in ["name is", "i am", "called"]):
                    cat_boost = 1.4
            elif any(w in msg_lower for w in ["phone", "email", "address", "contact"]):
                if category == "contact" or "@" in mem_lower:
                    cat_boost = 1.3
            elif any(w in msg_lower for w in ["like", "prefer", "favorite"]):
                if category == "preference":
                    cat_boost = 1.2

            kw_norm = min(kw_norm * cat_boost, 1.0)

            # Recency — tiebreaker only (max 5% contribution)
            ts = mem.get("timestamp", 0)
            days_old = max((now - ts) / 86400, 0)
            recency = 1.0 / (1.0 + days_old * 0.05)

            # Gate: need real relevance, not just recency
            if has_vector:
                if vs < 0.20 and kw_norm < 0.08:
                    continue
                final = (0.55 * vs) + (0.40 * kw_norm) + (0.05 * recency)
            else:
                if kw_norm < 0.08:
                    continue
                final = (0.95 * kw_norm) + (0.05 * recency)

            if final > 0.12:
                scored.append((final, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [mem for _, mem in scored[:k]]

    def build_context_preface(
        self,
        message: str,
        session: Any,
        use_web: bool = False,
        use_rag: bool = True,
        use_memory: bool = True,
        time_filter: Optional[str] = None,
        preset_system_prompt: Optional[str] = None,
        owner: Optional[str] = None,
        character_name: Optional[str] = None,
        agent_mode: bool = False,
        incognito: bool = False,
        use_skills: bool = True,
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]], List[Dict[str, str]]]:
        """Build the context preface for LLM calls.

        Returns:
            Tuple of (preface messages, rag_sources list)
        """
        preface = []
        rag_sources = []

        # Add preset system prompt if specified
        if preset_system_prompt:
            preface.append({
                "role": "system",
                "content": preset_system_prompt
            })
        preface.append({
            "role": "system",
            "content": UNTRUSTED_CONTEXT_POLICY,
        })

        # Memory: pinned (always included) + extended (RAG-retrieved when relevant)
        self._last_used_memories = []  # track what was injected
        if use_memory:
            mem_entries = self.memory_manager.load(owner=owner)

            pinned = [m for m in mem_entries if m.get("pinned")]
            extended = [m for m in mem_entries if not m.get("pinned")]

            _used_ids: list = []
            if pinned:
                pinned_text = "\n- ".join([m["text"] for m in pinned])
                preface.append(untrusted_context_message(
                    "saved memory: pinned user facts",
                    f"Core facts about the user:\n- {pinned_text}",
                ))
                for m in pinned:
                    self._last_used_memories.append({"text": m["text"], "category": m.get("category", "fact"), "type": "pinned"})
                    if m.get("id"):
                        _used_ids.append(m["id"])

            if extended:
                relevant = self._hybrid_retrieve(message, extended, k=3)
                if relevant:
                    ext_text = "\n".join([f"- {m['text']}" for m in relevant])
                    preface.append(untrusted_context_message(
                        "saved memory: retrieved context",
                        (
                            "Memory context. Do not reference unless the user asks "
                            f"about these topics.\n{ext_text}"
                        ),
                    ))
                    for m in relevant:
                        self._last_used_memories.append({"text": m["text"], "category": m.get("category", "fact"), "type": "recalled"})
                        if m.get("id"):
                            _used_ids.append(m["id"])

            # Bump usage counters for the memories that were actually injected.
            if _used_ids and hasattr(self.memory_manager, "increment_uses"):
                try:
                    self.memory_manager.increment_uses(_used_ids)
                except Exception as _e:
                    logger.warning("Failed to increment memory uses: %s", _e)

            # (skills index injection moved out — see below; only fires in
            # agent mode so chat mode and incognito stay clean.)

        local_doc_query = _local_document_search_query(message)

        # RAG: search if enabled and rag_manager available, inject only above threshold.
        # Explicit local-document searches are different from passive context:
        # they should return search evidence or a clear no-match result.
        if use_rag:
            try:
                rag_manager = getattr(self.personal_docs_manager, 'rag_manager', None)
                search_query = local_doc_query or message
                results = _rag_search(rag_manager, search_query, k=5, owner=owner) if rag_manager else []
                # Filter by similarity threshold for passive RAG. Explicit local
                # search keeps returned vector results, then uses keyword
                # fallback if vector search has no usable hit.
                if local_doc_query:
                    relevant = [r for r in results if r.get("document")]
                    if not relevant:
                        relevant = _keyword_document_results(self.personal_docs_manager, local_doc_query, k=5)
                else:
                    relevant = [r for r in results if r.get("similarity", 0) >= self.RAG_SIMILARITY_THRESHOLD]
                if local_doc_query:
                    preface.append({
                        "role": "system",
                        "content": (
                            "The user explicitly asked Cleverly to search local documents. "
                            "Use the local document search evidence when present. If no "
                            "matches are present, say that no matching indexed local "
                            "documents were found. Do not claim you cannot access local "
                            "files; explain that only indexed local documents are searched."
                        ),
                    })
                if relevant:
                    logger.info(f"RAG: {len(relevant)}/{len(results)} local document results")
                    rag_sources = [
                        {
                            "filename": _metadata_filename(r.get("metadata") or {}),
                            "snippet": str(r.get("document") or "")[:200],
                            "similarity": round(r.get("similarity", 0), 3)
                        }
                        for r in relevant
                    ]
                    heading = "Local document search results" if local_doc_query else "Relevant documents"
                    rag_content = f"{heading}:\n\n" + "\n\n---\n\n".join(
                        f"[{s['filename']}]\n{r['document']}" for s, r in zip(rag_sources, relevant)
                    )
                    if len(rag_content) > 10000:
                        rag_content = rag_content[:10000] + "\n[Truncated]"
                    preface.append(untrusted_context_message("retrieved documents", rag_content))
                elif local_doc_query:
                    preface.append({
                        "role": "system",
                        "content": (
                            f"Local document search completed for query '{local_doc_query}', "
                            "but no matching indexed local documents were found."
                        ),
                    })
            except Exception as e:
                logger.warning(f"RAG retrieval failed: {e}")
                if local_doc_query:
                    fallback = _keyword_document_results(self.personal_docs_manager, local_doc_query, k=5)
                    if fallback:
                        rag_sources = [
                            {
                                "filename": _metadata_filename(r.get("metadata") or {}),
                                "snippet": str(r.get("document") or "")[:200],
                                "similarity": round(r.get("similarity", 0), 3),
                            }
                            for r in fallback
                        ]
                        rag_content = "Local document keyword search results:\n\n" + "\n\n---\n\n".join(
                            f"[{s['filename']}]\n{r['document']}" for s, r in zip(rag_sources, fallback)
                        )
                        preface.append(untrusted_context_message("retrieved documents", rag_content))
                    else:
                        preface.append({
                            "role": "system",
                            "content": (
                                "Local document search was requested, but vector retrieval failed "
                                "and the local keyword index found no matches."
                            ),
                        })

        # Add web search if enabled
        web_sources = []
        if use_web:
            try:
                web_context, web_sources = comprehensive_web_search(
                    message, time_filter=time_filter, return_sources=True
                )
                preface.append(untrusted_context_message("web search results", web_context))
            except Exception as e:
                logger.error(f"Web search failed: {e}")
                preface.append({"role": "system", "content": "Web search encountered an error and could not retrieve results."})

        # Process non-YouTube URLs in message (YouTube handled by preprocess_message)
        # Skip auto-fetch for long pastes (the user already pasted the content —
        # fetching every embedded link buries the actual question under
        # hundreds of KB of duplicate page HTML and confuses the model) or for
        # link-heavy pastes (>3 URLs typically means it's a boilerplate-laden
        # blog post, not a "summarize this URL" request).
        urls = extract_urls(message)
        non_yt_urls = [u for u in urls if not is_youtube_url(u)]
        skip_url_fetch = len(message) > 2000 or len(non_yt_urls) > 3
        if not skip_url_fetch:
            for url in non_yt_urls:
                result = fetch_webpage_content(url)
                if result.get('success'):
                    content = result.get('content', '')[:10000]
                    preface.append(untrusted_context_message(
                        f"web page: {url}",
                        f"Content from {url}:\n\n{content}",
                    ))

        # Skills index — progressive disclosure. Only injected when the
        # model has the `manage_skills` tool available (agent_mode), and
        # never in incognito mode (the user has explicitly opted out of
        # context retention this turn). In plain chat mode the model can't
        # call the tool anyway, so the index would be noise.
        if agent_mode and not incognito and use_skills and self.skills_manager:
            try:
                idx = self.skills_manager.index_for(owner=owner)
            except Exception as e:
                logger.debug(f"Skills index unavailable: {e}")
                idx = []
            if idx:
                by_cat: Dict[str, list] = {}
                for s in idx:
                    by_cat.setdefault(s.get("category") or "general", []).append(s)
                lines = ["[Available skills — call manage_skills(action='view', name='...') to load one when relevant]"]
                for cat in sorted(by_cat):
                    lines.append(f"  {cat}:")
                    for s in sorted(by_cat[cat], key=lambda x: x["name"]):
                        desc = s.get("description") or ""
                        lines.append(f"    - {s['name']}: {desc}" if desc else f"    - {s['name']}")
                preface.append(untrusted_context_message("available skills index", "\n".join(lines)))

        return preface, rag_sources, web_sources
