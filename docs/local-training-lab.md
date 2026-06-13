# Cleverly Local Training Lab

The Training Lab is an offline-first starter workflow for experimenting with
language-model training concepts inside Cleverly.

## What It Does

- Saves pasted local text as datasets under `data/training/datasets`.
- Trains a tiny character n-gram model with default order `3`.
- Saves generated artifacts under `data/training/artifacts`.
- Generates sample text from a selected saved artifact.

## Offline Boundaries

The lab does not download datasets, install packages, start servers, call model
endpoints, or shell out to host commands. The implementation is pure Python and
uses only local files plus same-origin `/api/training/*` requests from the UI.

This is intentionally a small, safe first integration inspired by practical AI
engineering and from-scratch training repos. No code from those external repos
is vendored into Cleverly.

## Suggested Next Steps

After this starter lab is stable, the next useful additions are local file
import from already-mounted folders, a dataset preview/cleanup view, and an
optional advanced trainer that only activates when its dependencies are already
present in the offline image.

