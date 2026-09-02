# Phase 1 — Design brief

The model the redesigned front-end is built on. Written for a designer and for Claude
Design; the capability inventory covers mechanism, this covers meaning.

Figures come from the capability inventory run against the live library
(`app.openapi()`, 106 endpoints, 2026-08-31).

---

## 1. What this application is

**A library management tool, not a viewer.**

Playback is deliberately out of scope for this version. There is no watch state, no resume
position, no user model, and no "continue watching". The application exists so that one
person can see what is in their library, find what is wrong with it, and fix it.

That decision settles most of the information architecture:

- There is no home screen. The application opens into browse.
- Completeness is content. Missing artwork, missing release years, unclassified assets and
  failed transforms are not degraded states to hide behind fallbacks; they are the things
  the user came to find.
- The primary verbs are *find*, *inspect*, *correct* and *request*, not *play*.

## 2. Vocabulary

Terms as the API uses them. Where the user-facing word should differ, that is stated.

**Title** — the unit of identity. What a person thinks of as a DVD or Blu-ray package: a
coherent thing you would go looking for. A Title appears exactly once in a listing however
many files sit behind it. This is the deduplication concept and the reason the model exists.

**Asset** — the unit that exists on disk. A single media file, with streams, technical
metadata, external identifiers and a transform history.

**Title type** — Movie, Episode, Season, Collection, Music, Audiobook, Event, Other. Type
carries *watchability*: whether a Title is a thing you would consume (Movie, Episode) or a
thing you navigate through (Season, Collection). Eight types exist; six are in use.

> The type currently named `TV Show` denotes a single episode. It must be renamed to
> `Episode` before any UI copy is written, or the interface will say "12 TV Shows" when it
> means "12 episodes".

**Containment** — Titles contain Titles and Assets. Both edges are many-to-many, so the
structure is a directed acyclic graph rather than a tree. Cycles are rejected at write time.

**Membership kind** — every containment edge is either *intrinsic* or *curated*.

- *Intrinsic*: a fact about the work. Game of Thrones → Season 4 → S4E2. Defines the
  breadcrumb, is not user-editable, and is the only edge kind that aggregates should count.
- *Curated*: an editorial decision. "Oscar Winners 2023". Lateral rather than hierarchical,
  fully editable, reorderable, and surfaced separately from the library.

**`library_root`** — an explicit flag deciding whether a Title appears in the library grid.
Independent of type, because the four combinations are all real: a standalone film is
watchable and root; a series is root but not watchable; an episode is watchable but not
root; a season is neither.

**Tags** — attributes of works, including genre. Hierarchical. Tags are how a work is
classified; collections are not.

**Artwork** — attached per entity with a kind (poster, backdrop, and so on), one primary per
kind, with a resolution chain so a Title without its own artwork can resolve one.

**Stream** — an encoding, audio track or subtitle track within an Asset. Descriptive only.

**Transform request** — an asynchronous job against an Asset, with a status, a log and a
retry. The application both reports on these and creates them.

## 3. The starting state

The schema supports rich structure. The library currently in it is much flatter than the
schema allows. This is the condition the application exists to change, not the condition it
should be designed around.

| | |
|---|---|
| Titles | 1,597 |
| Assets | 13,321 |
| Containment edges | 1,907 — 1,398 to Assets, 509 to Titles |
| Fan-out per parent | median 1 · p95 2 · **max 35** |
| Titles that are a parent of something | 1,584 of 1,597 |
| Titles appearing under more than one parent | none yet |
| Assets appearing under more than one Title | none yet |
| Artwork rows | 1,205, against 1,597 titles — one entity type, one kind dominant |
| Titles with any tag | 858 of 1,597 · max 4 tags each · 9 of 46 tags used |
| `release_year` filled | 56% |
| `synopsis` filled | 96% |
| `title_references` | 0 rows — the feature exists and is unused |

Read together: the library is overwhelmingly **one Title, one Asset**, hierarchy is shallow,
multi-parent membership is unexercised, and roughly half the metadata a poster-wall design
would lean on is absent.

None of that is a specification. It is a backlog. The purpose of this application is to turn
13,321 loose assets into a structured library, so the design must make depth easy to build,
easy to see and easy to change — and must not treat the current flatness as the shape to
optimise for.

Design consequences:

- **Hierarchy is a first-class interaction, not a fallback.** Depth must be navigable and
  manipulable at every level. A design that handles two levels gracefully and four levels
  awkwardly fails at the primary job.
- **Distinguish flat-because-correct from flat-because-unorganised.** A standalone film with
  one asset is finished. Thirteen loose episode files are work. The interface must tell them
  apart, and the API must be able to answer the question (see the gap in §6).
- **Structure is built by direct manipulation.** Reorganising by opening a form, choosing a
  parent from a dropdown and saving is too slow for thousands of items. A tree with drag and
  drop, multi-select and keyboard movement is the working surface.
- **Never rely on artwork being present.** The grid needs a typographic treatment that is
  deliberate rather than a fallback, because it is currently the common case — and the
  absence is itself a work queue.
- **Design for multi-parent membership now.** No data exercises it yet; curated collections
  are the feature that will create it.
- Tag filtering is thin today (9 tags in real use, one covering 721 titles). Filter chips
  with counts would currently show one enormous bucket.

## 4. Surfaces

**Library.** The primary surface. `library_root=true` Titles only. Virtualised infinite
scroll on a keyset cursor, page cap 500, measured p95 140ms at 200 rows. Filterable by
type, tag, parent, membership kind, and artwork presence. Sortable by name, type and id.
Search is a substring match on name that requires **three characters** before the trigram
index engages, so the search box must not fire before then.

This surface is also the gap-finding tool: "roots with no artwork", "titles with no year",
"titles with no tags" are first-class views, not advanced filters buried in a panel.

**Title.** Identity, artwork, synopsis, year, tags, external identifiers. Its contents,
separated by membership kind: intrinsic children as structure, curated parents shown as
"also appears in". Its Assets, distinguished as alternative renderings of the same content
versus separate things.

**Asset.** A full destination, not a sub-panel. Path, technical metadata, streams (encodings,
languages, subtitle tracks), external identifiers, derived assets, accessories, artwork, the
Titles it belongs to, and its transform history. This is where correction work happens.

**Operations.** Transform requests across the whole library: queue state, failures, retries,
logs. Asynchronous work that the user initiates from an Asset and monitors here. There is no
push channel, so the front-end polls; the polling contract is an open decision below.

**Organise.** The surface where structure gets built, and the reason the application exists.
A tree of intrinsic containment, expandable and navigable to arbitrary depth, alongside a
working set of unplaced material. Drag and drop to attach and to move, multi-select for bulk
placement, keyboard movement, reorder within a parent, and detach.

Requirements this surface imposes:

- Depth is unbounded in principle. Expand and collapse state must persist across navigation,
  and position within a large tree must survive a refresh.
- A move is one gesture and must read as one action, whatever it costs in requests.
- Illegal drops are prevented before the drop, not reported after it. Dropping a Title onto
  its own descendant is rejected by the API; the interface should never offer it.
- Every destructive gesture distinguishes detach from delete.
- Bulk placement of a multi-selection needs a progress and partial-failure model, because
  the API applies these one edge at a time (see §6).
- Undo, or a recently-changed list precise enough to reverse a mistake by hand.

**Curated collections.** Separate from the library grid. Create, add, remove, reorder. The
same manipulation vocabulary as Organise, applied to curated rather than intrinsic edges.

## 5. Principles

1. **A Title appears once.** Everything else in the model exists to serve this.
2. **Gaps are content.** Surface what is missing as prominently as what is present.
3. **Derive what can be derived, ask about what cannot.** Choosing between a 1080p and a
   4K encoding of the same content is the application's job. Choosing between a theatrical
   and a director's cut is the user's.
4. **Remove is not delete.** With many-to-many membership, every removal affordance must be
   unambiguous about whether it detaches an edge or destroys an object.
5. **Breadcrumbs follow intrinsic edges only.** Curated membership is lateral and never
   appears in the path.
6. **Aggregates deduplicate.** Counts and totals follow intrinsic edges and dedupe by id.
7. **Write failure is a first-class state.** Some writes touch the filesystem as well as the
   database and can half-succeed. The interface must say so rather than showing a spinner
   that resolves into a lie.
8. **The library's current shape is the workload, not the specification.** Every measurement
   in §3 describes what needs fixing. Design for the library as it should be, and make the
   distance between the two visible and shrinkable.

## 6. API gaps blocking the Organise surface

Four, all newly identified and none yet raised as issues.

- **No way to ask what is unplaced.** `GET /api/assets/` has no filter for whether an Asset
  belongs to any Title, and there is no equivalent for Titles with no intrinsic parent that
  are not library roots. This is the work queue for the entire organising workflow and it
  cannot currently be requested.
- **No move operation.** Changing a parent means `DELETE` on the old edge followed by `POST`
  of the new one: two requests, no transaction, and a failure between them leaves the item
  attached to nothing. A move is the core gesture of a tree UI and it needs to be one call.
- **No bulk containment writes.** Every edge is created, moved or removed individually.
  Dragging forty episodes into a season is forty requests with no atomicity and a partial
  failure mode the interface has to render.
- **Reorder is per-edge.** `uq_parent_position` enforces position uniqueness within a parent,
  and reordering is a single-edge operation. Whether a sibling set can be reordered without
  transient constraint collisions needs establishing before a drag-to-reorder is designed.

## 7. Open decisions

- **Asset alternatives.** Sibling Assets under one Title cover both encodings of the same
  content and genuinely different editions. Edition is not yet a field (issue M3). Until it
  is, the Title screen cannot decide between one action and a picker.
- **Polling contract.** Interval, backoff, and whether the operations surface polls the
  collection or individual requests.
- **Curated collection entry point.** Its own top-level surface, or a mode within the
  library.
- **Search.** Unified search is in progress. The UI should be built against the existing
  name filter and adopt the unified endpoint when it lands, rather than waiting.
- **Empty-state treatment for artwork.** Typographic, generated, or kind-derived.

## 8. Explicitly out of scope

Playback and anything downstream of it: watch state, resume, progress, per-user
personalisation, recommendations, continue-watching. Multi-user and any authorisation model
beyond the existing bearer token.
