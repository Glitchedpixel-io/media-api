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

> **Done.** This type was called `TV Show` while denoting a single episode, which would
> have had the interface saying "12 TV Shows" when it meant "12 episodes". Migration
> `2d7e94fb015a` renamed the code to `episode` and the label to `Episode`, and both are
> live. UI copy should take the label from the API rather than hard-coding it, so the next
> rename does not need a second pass over the front-end.

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

## 3. The shape of the actual data

The schema supports rich structure. The library currently in it is much flatter than the
schema allows, and the design must be built for what is there rather than for what the model
permits.

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

Read together: the library is overwhelmingly **one Title, one Asset**. Deep hierarchy is
rare, multi-parent membership is currently theoretical, and roughly half the metadata a
poster-wall design would lean on is absent.

Design consequences:

- Optimise for the flat case; support hierarchy without making it the organising metaphor.
  A design that presents itself as a series browser will look broken against this data.
- Never rely on artwork being present. The grid needs a typographic treatment that is
  deliberate rather than a fallback, because it will be the common case.
- Multi-parent membership must be *designed for* even though no data exercises it yet, since
  curated collections are the feature that will create it.
- Tag-based filtering is thin today (9 tags in real use, one covering 721 titles). Filter
  chips with counts would currently show one enormous bucket.

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

**Curated collections.** Separate from the library grid. Create, add, remove, reorder.

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

## 6. Open decisions

- **Asset alternatives.** Sibling Assets under one Title cover both encodings of the same
  content and genuinely different editions. `assets.edition` now exists — migration
  `6b1f8ac340d9` added it, closing M3, and it is returned on every Asset payload. The
  decision still stands, for a different reason than before: the field is filled for **146
  of 13,344 assets (1.1%, 10 distinct values)**, so a null edition does not distinguish
  "same content, different encoding" from "a different cut" — it means nobody has said
  yet. The Title screen can read and display an edition where one exists, and cannot use
  its absence to choose between one action and a picker until the column is backfilled.
- **Polling contract.** Interval, backoff, and whether the operations surface polls the
  collection or individual requests.
- **Curated collection entry point.** Its own top-level surface, or a mode within the
  library.
- **Search.** Unified search is in progress. The UI should be built against the existing
  name filter and adopt the unified endpoint when it lands, rather than waiting.
- **Empty-state treatment for artwork.** Typographic, generated, or kind-derived.

## 7. Explicitly out of scope

Playback and anything downstream of it: watch state, resume, progress, per-user
personalisation, recommendations, continue-watching. Multi-user and any authorisation model
beyond the existing bearer token.
