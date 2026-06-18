import base64
import datetime as dt
import zipfile
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


class Expr:
    def __init__(self, field=None, value=None, op="eq"):
        self.field = field
        self.value = value
        self.op = op

    def __or__(self, other):
        return Expr(None, (self, other), "or")

    def __and__(self, other):
        return Expr(None, (self, other), "and")


class SumExpr:
    def __init__(self, field):
        self.field = field


class Sort:
    def __init__(self, field, reverse=False):
        self.field = field
        self.reverse = reverse


class Column:
    def __init__(self, name, owner=None):
        self.name = name
        self.owner = owner

    def __eq__(self, other):
        return Expr(self.name, other)

    def __ne__(self, other):
        return Expr(self.name, other, "ne")

    def in_(self, values):
        return Expr(self.name, set(values), "in")

    def ilike(self, value):
        return Expr(self.name, value, "ilike")

    def like(self, value):
        return Expr(self.name, value, "like")

    def is_(self, value):
        return Expr(self.name, value, "is")

    def isnot(self, value):
        return Expr(self.name, value, "isnot")

    def asc(self):
        return Sort(self.name)

    def desc(self):
        return Sort(self.name, reverse=True)


class FakeGalleryImage:
    id = Column("id", "image")
    filename = Column("filename", "image")
    prompt = Column("prompt", "image")
    model = Column("model", "image")
    tags = Column("tags", "image")
    ai_tags = Column("ai_tags", "image")
    session_id = Column("session_id", "image")
    album_id = Column("album_id", "image")
    owner = Column("owner", "image")
    is_active = Column("is_active", "image")
    favorite = Column("favorite", "image")
    file_hash = Column("file_hash", "image")
    file_size = Column("file_size", "image")
    created_at = Column("created_at", "image")
    updated_at = Column("updated_at", "image")

    def __init__(self, **kwargs):
        now = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
        self.id = kwargs.get("id", "img-new")
        self.filename = kwargs.get("filename", "image.png")
        self.prompt = kwargs.get("prompt", "Photo")
        self.model = kwargs.get("model", "imported")
        self.size = kwargs.get("size", "1024x1024")
        self.quality = kwargs.get("quality", "standard")
        self.tags = kwargs.get("tags", "")
        self.ai_tags = kwargs.get("ai_tags", "")
        self.session_id = kwargs.get("session_id", "s1")
        self.album_id = kwargs.get("album_id")
        self.owner = kwargs.get("owner", "alice")
        self.is_active = kwargs.get("is_active", True)
        self.favorite = kwargs.get("favorite", False)
        self.file_hash = kwargs.get("file_hash", "")
        self.taken_at = kwargs.get("taken_at")
        self.camera_make = kwargs.get("camera_make")
        self.camera_model = kwargs.get("camera_model")
        self.gps_lat = kwargs.get("gps_lat")
        self.gps_lng = kwargs.get("gps_lng")
        self.width = kwargs.get("width")
        self.height = kwargs.get("height")
        self.file_size = kwargs.get("file_size", 0)
        self.created_at = kwargs.get("created_at", now)
        self.updated_at = kwargs.get("updated_at", now)


class FakeGalleryAlbum:
    id = Column("id", "album")
    name = Column("name", "album")
    owner = Column("owner", "album")
    cover_id = Column("cover_id", "album")
    created_at = Column("created_at", "album")

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "album-new")
        self.name = kwargs.get("name", "Album")
        self.description = kwargs.get("description", "")
        self.cover_id = kwargs.get("cover_id")
        self.owner = kwargs.get("owner", "alice")
        self.created_at = kwargs.get(
            "created_at", dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        )


class FakeModelEndpoint:
    model_type = Column("model_type", "endpoint")
    is_enabled = Column("is_enabled", "endpoint")

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "ep1")
        self.model_type = kwargs.get("model_type", "image")
        self.is_enabled = kwargs.get("is_enabled", True)
        self.base_url = kwargs.get("base_url", "http://localhost:7860")


class FakeDbSession:
    id = Column("id", "session")
    name = Column("name", "session")

    def __init__(self, id="s1", name="Main"):
        self.id = id
        self.name = name


class FakeQuery:
    def __init__(self, db, items=None, *, row_mode=False, column=None, scalar_field=None):
        self.db = db
        self.items = list(items or [])
        self.row_mode = row_mode
        self.column = column
        self.scalar_field = scalar_field

    def _target(self, item):
        return item[0] if isinstance(item, tuple) else item

    def _value(self, item, field):
        return getattr(self._target(item), field, None)

    def _matches(self, item, condition):
        if condition is True or condition is None:
            return True
        if condition is False:
            return False
        if not isinstance(condition, Expr):
            return True
        if condition.op == "or":
            return any(self._matches(item, sub) for sub in condition.value)
        if condition.op == "and":
            return all(self._matches(item, sub) for sub in condition.value)
        value = self._value(item, condition.field)
        if condition.op in {"eq", "is"}:
            return value == condition.value
        if condition.op == "ne":
            return value != condition.value
        if condition.op == "isnot":
            return value is not condition.value
        if condition.op == "in":
            return value in condition.value
        if condition.op in {"ilike", "like"}:
            needle = str(condition.value).strip("%").lower()
            return needle in str(value or "").lower()
        return True

    def _clone(self, items=None):
        return FakeQuery(
            self.db,
            self.items if items is None else items,
            row_mode=self.row_mode,
            column=self.column,
            scalar_field=self.scalar_field,
        )

    def filter(self, *conditions):
        items = list(self.items)
        for condition in conditions:
            items = [item for item in items if self._matches(item, condition)]
        return self._clone(items)

    def outerjoin(self, *args, **kwargs):
        return self

    def order_by(self, *sorts):
        items = list(self.items)
        for sort in reversed([s for s in sorts if isinstance(s, Sort)]):
            items.sort(key=lambda item: self._value(item, sort.field) or dt.datetime.min, reverse=sort.reverse)
        return self._clone(items)

    def offset(self, offset):
        return self._clone(self.items[offset:])

    def limit(self, limit):
        return self._clone(self.items[:limit])

    def distinct(self):
        if not self.column:
            return self
        seen = set()
        unique = []
        for item in self.items:
            value = self._value(item, self.column)
            if value not in seen:
                seen.add(value)
                unique.append(item)
        return self._clone(unique)

    def with_entities(self, column):
        return FakeQuery(self.db, self.items, column=column.name)

    def update(self, values, synchronize_session=False):
        for item in self.items:
            target = self._target(item)
            for key, value in values.items():
                setattr(target, key, value)
        return len(self.items)

    def all(self):
        if self.scalar_field:
            return [(self.scalar(),)]
        if self.column:
            return [(self._value(item, self.column),) for item in self.items]
        if self.row_mode:
            return [(img, self.db.session_name(img.session_id)) for img in self.items]
        return self.items

    def first(self):
        rows = self.all() if (self.column or self.row_mode) else self.items
        return rows[0] if rows else None

    def count(self):
        return len(self.items)

    def scalar(self):
        if not self.scalar_field:
            return None
        return sum((getattr(item, self.scalar_field, 0) or 0) for item in self.items)


class FakeGalleryDB:
    def __init__(self):
        old = dt.datetime(2025, 12, 31, tzinfo=dt.timezone.utc)
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        self.sessions = [FakeDbSession("s1", "Session One")]
        self.images = [
            FakeGalleryImage(
                id="img1",
                filename="img1.png",
                prompt="Beach sunrise",
                model="GLM-5.2",
                tags="sun, travel",
                ai_tags="sky",
                album_id="album1",
                favorite=True,
                file_size=1500,
                width=3,
                height=2,
                camera_make="Fuji",
                camera_model="X100",
                gps_lat="1.000000",
                gps_lng="2.000000",
                created_at=now,
            ),
            FakeGalleryImage(
                id="img2",
                filename="img2.png",
                prompt="Desk setup",
                model="llama3.2:3b",
                tags="desk",
                ai_tags=None,
                album_id=None,
                favorite=False,
                file_size=500,
                created_at=old,
            ),
            FakeGalleryImage(id="bob", filename="bob.png", owner="bob", tags="private", file_size=200),
            FakeGalleryImage(id="inactive", filename="old.png", is_active=False, tags="old"),
        ]
        self.albums = [
            FakeGalleryAlbum(id="album1", name="Travel", description="Trips", owner="alice", cover_id="img1"),
            FakeGalleryAlbum(id="album2", name="Work", owner="alice"),
            FakeGalleryAlbum(id="bob-album", name="Private", owner="bob"),
        ]
        self.endpoints = [FakeModelEndpoint()]
        self.added = []
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def session_name(self, session_id):
        session = next((s for s in self.sessions if s.id == session_id), None)
        return session.name if session else None

    def query(self, *models):
        first = models[0]
        if first is FakeGalleryImage:
            return FakeQuery(self, self.images, row_mode=len(models) > 1)
        if first is FakeGalleryAlbum:
            return FakeQuery(self, self.albums)
        if first is FakeModelEndpoint:
            return FakeQuery(self, self.endpoints)
        if first is FakeDbSession:
            return FakeQuery(self, self.sessions)
        if isinstance(first, Column):
            source = self.images if first.owner == "image" else self.albums
            return FakeQuery(self, source, column=first.name)
        if isinstance(first, SumExpr):
            return FakeQuery(self, self.images, scalar_field=first.field)
        return FakeQuery(self, [])

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, FakeGalleryImage):
            self.images.append(obj)
        elif isinstance(obj, FakeGalleryAlbum):
            self.albums.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)
        if obj in self.albums:
            self.albums.remove(obj)
        if obj in self.images:
            self.images.remove(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, _obj):
        return None

    def close(self):
        self.closed += 1


class RequestLike:
    def __init__(self, *, user="alice", body=None, form_data=None):
        self.state = SimpleNamespace(current_user=user)
        self._body = body or {}
        self._form = form_data or {}

    async def json(self):
        return self._body

    async def form(self):
        return self._form


class UploadLike:
    def __init__(self, filename, content):
        self.filename = filename
        self._content = content

    async def read(self):
        return self._content


def _png_bytes(size=(4, 2), color=(0, 128, 255)):
    Image = pytest.importorskip("PIL.Image")
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def gallery_env(monkeypatch, tmp_path):
    import sqlalchemy
    import routes.gallery_helpers as helpers
    import routes.gallery_routes as routes

    db = FakeGalleryDB()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(routes, "GalleryImage", FakeGalleryImage)
    monkeypatch.setattr(routes, "GalleryAlbum", FakeGalleryAlbum)
    monkeypatch.setattr(routes, "ModelEndpoint", FakeModelEndpoint)
    monkeypatch.setattr(routes, "DbSession", FakeDbSession)
    monkeypatch.setattr(routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(routes, "require_privilege", lambda request, privilege: request.state.current_user)
    monkeypatch.setattr(helpers, "GalleryImage", FakeGalleryImage)
    monkeypatch.setattr(sqlalchemy, "or_", lambda *conditions: Expr(None, conditions, "or"))
    monkeypatch.setattr(sqlalchemy, "func", SimpleNamespace(sum=lambda column: SumExpr(column.name)))
    return SimpleNamespace(router=routes.setup_gallery_routes(), db=db, tmp_path=tmp_path)


def test_gallery_helpers_extract_full_exif_and_large_size(monkeypatch):
    import routes.gallery_helpers as helpers

    Image = pytest.importorskip("PIL.Image")

    class FakeExifImage:
        width = 4032
        height = 3024

        def _getexif(self):
            return {
                271: " FujiFilm ",
                272: " X100VI ",
                36867: "not a date",
                36868: "2026:06:13 15:13:23",
                34853: {
                    1: "S",
                    2: (35, 30, 0),
                    3: "W",
                    4: (97, 15, 30),
                },
            }

    monkeypatch.setattr(Image, "open", lambda _stream: FakeExifImage())

    result = helpers._extract_exif(b"image-bytes")

    assert result["width"] == 4032
    assert result["height"] == 3024
    assert result["camera_make"] == "FujiFilm"
    assert result["camera_model"] == "X100VI"
    assert result["taken_at"] == dt.datetime(2026, 6, 13, 15, 13, 23)
    assert result["gps_lat"] == "-35.500000"
    assert result["gps_lng"] == "-97.258333"
    assert helpers._human_size(1024**6) == "1024.0 PB"


def test_gallery_helpers_ignores_bad_gps_exif(monkeypatch):
    import routes.gallery_helpers as helpers

    Image = pytest.importorskip("PIL.Image")

    class FakeBadGpsImage:
        width = 10
        height = 20

        def _getexif(self):
            return {34853: {2: ("bad", 0, 0), 4: (1, 2, 3)}}

    monkeypatch.setattr(Image, "open", lambda _stream: FakeBadGpsImage())

    result = helpers._extract_exif(b"image-bytes")

    assert result["width"] == 10
    assert result["height"] == 20
    assert "gps_lat" not in result
    assert "gps_lng" not in result


def test_gallery_helpers_owner_filter_blocks_anonymous():
    import routes.gallery_helpers as helpers

    class Query:
        def __init__(self):
            self.condition = None

        def filter(self, condition):
            self.condition = condition
            return self

    query = Query()

    assert helpers._owner_filter(query, None) is query
    assert query.condition is False


def test_gallery_offline_endpoint_guards(monkeypatch):
    import routes.gallery_routes as routes

    monkeypatch.setattr(routes, "offline_mode", lambda: True)
    monkeypatch.setattr(routes, "is_local_model_url", lambda url: "localhost" in url)

    routes._ensure_offline_local_endpoint("http://localhost:8100/v1", "image endpoint")
    routes._ensure_offline_local_image_url("http://localhost:8100/image.png")

    with pytest.raises(HTTPException) as endpoint_exc:
        routes._ensure_offline_local_endpoint("https://api.openai.com/v1", "image endpoint")
    assert endpoint_exc.value.status_code == 403

    with pytest.raises(HTTPException) as image_exc:
        routes._ensure_offline_local_image_url("https://cdn.example/image.png")
    assert image_exc.value.status_code == 502


@pytest.mark.asyncio
async def test_gallery_offline_blocks_image_model_weight_downloads(gallery_env, monkeypatch):
    import routes.gallery_routes as routes

    monkeypatch.setattr(routes, "offline_mode", lambda: True)
    image = base64.b64encode(_png_bytes()).decode()

    for path in [
        "/api/image/denoise",
        "/api/image/upscale-local",
        "/api/image/remove-bg",
    ]:
        endpoint = _endpoint(gallery_env.router, path, "POST")
        with pytest.raises(HTTPException) as exc:
            await endpoint(RequestLike(body={"image": image}))
        assert exc.value.status_code == 403

    enhance = _endpoint(gallery_env.router, "/api/image/enhance-face", "POST")
    result = await enhance(RequestLike(body={"image": image}))
    assert result["method"] == "pil"
    assert base64.b64decode(result["image"])


@pytest.mark.asyncio
async def test_gallery_upload_replace_rename_rotate_and_helpers(gallery_env):
    import routes.gallery_helpers as helpers

    image = gallery_env.db.images[0]
    image.taken_at = dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)
    payload = helpers._image_to_dict(image, "Vacation")
    assert payload["session_name"] == "Vacation"
    assert payload["camera"] == "Fuji X100"
    assert payload["gps"] == {"lat": "1.000000", "lng": "2.000000"}
    assert helpers._human_size(1536) == "1.5 KB"
    assert "exif_error" in helpers._extract_exif(b"not an image")

    upload = _endpoint(gallery_env.router, "/api/gallery/upload", "POST")
    png = _png_bytes()
    result = await upload(
        RequestLike(
            form_data={"file": UploadLike("photo.png", png), "album_id": "album1"},
        )
    )
    created = next(img for img in gallery_env.db.images if img.id == result["id"])
    created_filename = created.filename
    assert result["ok"] is True
    assert created.owner == "alice"
    assert created.album_id == "album1"

    duplicate = await upload(RequestLike(form_data={"file": UploadLike("photo.png", png)}))
    assert duplicate["duplicate"] is True
    assert duplicate["id"] == created.id

    with pytest.raises(HTTPException) as unsupported:
        await upload(RequestLike(form_data={"file": UploadLike("payload.exe", b"binary")}))
    assert unsupported.value.status_code == 400

    replace = _endpoint(gallery_env.router, "/api/gallery/{image_id}/replace", "POST")
    created.filename = "../outside.png"
    with pytest.raises(HTTPException) as unsafe_replace:
        await replace(
            RequestLike(form_data={"image": UploadLike("replacement.png", _png_bytes((2, 5)))}),
            created.id,
        )
    assert unsafe_replace.value.status_code == 400
    created.filename = created_filename
    replaced = await replace(
        RequestLike(form_data={"image": UploadLike("replacement.png", _png_bytes((2, 5)))}),
        created.id,
    )
    assert replaced == {"ok": True, "width": 2, "height": 5}

    rename = _endpoint(gallery_env.router, "/api/gallery/{image_id}/rename", "POST")
    with pytest.raises(HTTPException) as empty_name:
        await rename(RequestLike(body={"name": "   "}), created.id)
    assert empty_name.value.status_code == 400
    assert await rename(RequestLike(body={"name": " Clean Name "}), created.id) == {
        "ok": True,
        "name": "Clean Name",
    }

    rotate = _endpoint(gallery_env.router, "/api/gallery/{image_id}/rotate", "POST")
    with pytest.raises(HTTPException) as bad_angle:
        await rotate(RequestLike(body={"angle": "sideways"}), created.id)
    assert bad_angle.value.status_code == 400
    created.filename = "../outside.png"
    with pytest.raises(HTTPException) as unsafe_rotate:
        await rotate(RequestLike(body={"angle": 90}), created.id)
    assert unsafe_rotate.value.status_code == 400
    created.filename = created_filename
    rotated = await rotate(RequestLike(body={"angle": 90}), created.id)
    assert rotated["ok"] is True
    assert (created.width, created.height) == (5, 2)


@pytest.mark.asyncio
async def test_gallery_library_albums_tags_stats_patch_and_cleanup(gallery_env):
    tags = _endpoint(gallery_env.router, "/api/gallery/tags", "GET")
    assert await tags(RequestLike()) == {"tags": ["desk", "sun", "travel"]}

    library = _endpoint(gallery_env.router, "/api/gallery/library", "GET")
    filtered = await library(
        RequestLike(),
        search="beach",
        tag="sun, sky",
        model="GLM-5.2",
        album="album1",
        favorites=True,
        sort="shuffle",
        seed=42,
        offset=0,
        limit=10,
    )
    assert [item["id"] for item in filtered["items"]] == ["img1"]
    assert filtered["total"] == 1
    assert filtered["models"] == ["GLM-5.2", "llama3.2:3b"]

    oldest = await library(
        RequestLike(),
        search=None,
        tag=None,
        model=None,
        album=None,
        favorites=False,
        sort="oldest",
        seed=None,
        offset=0,
        limit=1,
    )
    assert oldest["items"][0]["id"] == "img2"

    list_albums = _endpoint(gallery_env.router, "/api/gallery/albums", "GET")
    albums = await list_albums(RequestLike())
    assert [album["id"] for album in albums["albums"]] == ["album1", "album2"]
    assert albums["albums"][0]["cover_url"] == "/api/generated-image/img1.png"

    create_album = _endpoint(gallery_env.router, "/api/gallery/albums", "POST")
    with pytest.raises(HTTPException) as missing_name:
        await create_album(RequestLike(body={"name": ""}))
    assert missing_name.value.status_code == 400
    created_album = await create_album(RequestLike(body={"name": " New Album ", "description": "desc"}))
    assert created_album["name"] == "New Album"

    stats = _endpoint(gallery_env.router, "/api/gallery/stats", "GET")
    stat_result = await stats(RequestLike())
    assert stat_result["total_photos"] == 2
    assert stat_result["favorites"] == 1
    assert stat_result["albums"] == 3
    assert stat_result["total_size"] == 2000

    batch = _endpoint(gallery_env.router, "/api/gallery/ai-tag-batch", "POST")
    batch_result = await batch(RequestLike(), album_id=None, limit=999)
    assert batch_result["image_ids"] == ["img2"]
    assert batch_result["queued"] == 1

    get_image = _endpoint(gallery_env.router, "/api/gallery/{image_id}", "GET")
    image_result = await get_image(RequestLike(), "img1")
    assert image_result["session_name"] == "Session One"
    with pytest.raises(HTTPException) as hidden:
        await get_image(RequestLike(user="bob"), "img1")
    assert hidden.value.status_code == 404

    patch_image = _endpoint(gallery_env.router, "/api/gallery/{image_id}", "PATCH")
    from routes.gallery_helpers import GalleryPatch

    patched = await patch_image(
        RequestLike(),
        "img1",
        GalleryPatch(tags="sky, Custom, custom, Travel", favorite=False, album_id=""),
    )
    assert patched["tags"] == "Custom, Travel"
    assert patched["favorite"] is False
    assert gallery_env.db.images[0].album_id is None

    gallery_env.db.images[0].tags = "Sky, Travel, Travel, Custom"
    gallery_env.db.images[0].ai_tags = "sky"
    dedupe = _endpoint(gallery_env.router, "/api/gallery/dedupe-tags", "POST")
    assert await dedupe(RequestLike()) == {"ok": True, "rows_touched": 1, "tags_removed": 2}

    clear_user = _endpoint(gallery_env.router, "/api/gallery/clear-user-tags", "POST")
    assert (await clear_user(RequestLike()))["cleared"] == 2

    clear_ai = _endpoint(gallery_env.router, "/api/gallery/clear-ai-tags", "POST")
    assert await clear_ai(RequestLike(), image_id="img1") == {"ok": True, "cleared": 1}

    favorite = _endpoint(gallery_env.router, "/api/gallery/{image_id}/favorite", "POST")
    toggled = await favorite(RequestLike(), "img1")
    assert toggled == {"ok": True, "favorite": True}


@pytest.mark.asyncio
async def test_gallery_zip_album_delete_and_image_tools(gallery_env):
    image_dir = gallery_env.tmp_path / "data" / "generated_images"
    image_dir.mkdir(parents=True)
    (image_dir / "img1.png").write_bytes(_png_bytes())

    download_zip = _endpoint(gallery_env.router, "/api/gallery/download-zip", "POST")
    response = await download_zip(RequestLike(body={"ids": ["img1"]}))
    assert response.media_type == "application/zip"
    with zipfile.ZipFile(BytesIO(response.body)) as zf:
        assert zf.namelist() == ["Beach sunrise.png"]

    with pytest.raises(HTTPException) as anonymous:
        await download_zip(RequestLike(user=None, body={"ids": ["img1"]}))
    assert anonymous.value.status_code == 401
    with pytest.raises(HTTPException) as no_ids:
        await download_zip(RequestLike(body={"ids": []}))
    assert no_ids.value.status_code == 400
    with pytest.raises(HTTPException) as missing_file:
        await download_zip(RequestLike(body={"ids": ["img2"]}))
    assert missing_file.value.status_code == 404
    (image_dir.parent / "secret.txt").write_text("do not zip", encoding="utf-8")
    gallery_env.db.images[1].filename = "../secret.txt"
    with pytest.raises(HTTPException) as traversal_file:
        await download_zip(RequestLike(body={"ids": ["img2"]}))
    assert traversal_file.value.status_code == 404

    add_album = _endpoint(gallery_env.router, "/api/gallery/albums/{album_id}/add", "POST")
    assert await add_album(RequestLike(body={"image_ids": ["img2"]}), "album2") == {"ok": True, "count": 1}
    assert gallery_env.db.images[1].album_id == "album2"

    remove_album = _endpoint(gallery_env.router, "/api/gallery/albums/{album_id}/remove", "POST")
    assert await remove_album(RequestLike(body={"image_ids": ["img2"]}), "album2") == {"ok": True}
    assert gallery_env.db.images[1].album_id is None

    update_album = _endpoint(gallery_env.router, "/api/gallery/albums/{album_id}", "PUT")
    assert await update_album(
        RequestLike(body={"name": "Updated", "description": "fresh", "cover_id": "img1"}),
        "album2",
    ) == {"ok": True}
    assert gallery_env.db.albums[1].name == "Updated"
    with pytest.raises(HTTPException) as bad_cover:
        await update_album(RequestLike(body={"cover_id": "bob"}), "album2")
    assert bad_cover.value.status_code == 404

    delete_album = _endpoint(gallery_env.router, "/api/gallery/albums/{album_id}", "DELETE")
    assert await delete_album(RequestLike(), "album2") == {"ok": True}
    assert all(album.id != "album2" for album in gallery_env.db.albums)

    delete_image = _endpoint(gallery_env.router, "/api/gallery/{image_id}", "DELETE")
    assert await delete_image(RequestLike(), "img2") == {"status": "deleted", "id": "img2"}
    assert gallery_env.db.images[1].is_active is False
    assert (image_dir.parent / "secret.txt").exists()
    assert await delete_image(RequestLike(), "img1") == {"status": "deleted", "id": "img1"}
    assert gallery_env.db.images[0].is_active is False
    assert not (image_dir / "img1.png").exists()
    with pytest.raises(HTTPException) as missing_image:
        await delete_image(RequestLike(), "missing")
    assert missing_image.value.status_code == 404

    sharpen = _endpoint(gallery_env.router, "/api/image/sharpen", "POST")
    sharpened = await sharpen(
        RequestLike(body={"image": base64.b64encode(_png_bytes()).decode(), "amount": 75})
    )
    assert base64.b64decode(sharpened["image"])

    denoise = _endpoint(gallery_env.router, "/api/image/denoise", "POST")
    with pytest.raises(HTTPException) as no_image:
        await denoise(RequestLike(body={}))
    assert no_image.value.status_code == 400
