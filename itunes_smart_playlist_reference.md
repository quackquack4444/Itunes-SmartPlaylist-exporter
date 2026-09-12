# iTunes/Music.app Smart Playlist Binary Format — Reference Tables

Everything below is reverse-engineered from real `--debug` hex dumps against
one specific Music.app library, and cross-checked against `itunes_smart_to_sql.py`.
Field identities are complete (every field in the Music.app filter dropdown has
been confirmed at least once); some enum/value details remain open — see the
"Open questions" section at the end.

## Field codes

| Hex | Dec | Column name | Value type | Notes |
|---|---|---|---|---|
| 0x02 | 2 | name | string | |
| 0x03 | 3 | album | string | |
| 0x04 | 4 | artist | string | |
| 0x05 | 5 | bit_rate | numeric | kbps |
| 0x06 | 6 | sample_rate | numeric | Hz |
| 0x07 | 7 | year | numeric | |
| 0x08 | 8 | genre | string | |
| 0x09 | 9 | kind | string | |
| 0x0A | 10 | date_modified | date | Mac absolute time |
| 0x0B | 11 | track_number | numeric | |
| 0x0C | 12 | size | numeric | bytes |
| 0x0D | 13 | time | duration_ms | **milliseconds** |
| 0x0E | 14 | comments | string | |
| 0x10 | 16 | date_added | date | Mac absolute time |
| 0x12 | 18 | composer | string | |
| 0x16 | 22 | plays | numeric | play count |
| 0x17 | 23 | last_played | date | Mac absolute time |
| 0x18 | 24 | disc_number | numeric | |
| 0x19 | 25 | rating | rating | x20 scale, see bucket formula below |
| 0x1D | 29 | ticked | ticked | anomalous negation — see Open questions |
| 0x1F | 31 | compilation | boolean | |
| 0x23 | 35 | bpm | numeric | |
| 0x25 | 37 | has_artwork | boolean | |
| 0x27 | 39 | grouping | string | |
| 0x28 | 40 | playlist_ref | playlist_ref | value = 8-byte Playlist Persistent ID |
| 0x29 | 41 | purchased | boolean | |
| 0x36 | 54 | description | string | |
| 0x37 | 55 | category | string | (podcast category) |
| 0x3C | 60 | media_kind | media_kind | **bitmask** enum, see below |
| 0x3E | 62 | programme | string | distinct from Sort Programme |
| 0x3F | 63 | series | numeric | |
| 0x44 | 68 | skips | numeric | skip count |
| 0x45 | 69 | last_skipped | date | Mac absolute time |
| 0x47 | 71 | album_artist | string | |
| 0x4E | 78 | sort_name | string | |
| 0x4F | 79 | sort_album | string | |
| 0x50 | 80 | sort_artist | string | |
| 0x51 | 81 | sort_album_artist | string | |
| 0x52 | 82 | sort_composer | string | |
| 0x53 | 83 | sort_programme | string | |
| 0x5A | 90 | album_rating | rating | x20 scale, see bucket formula below |
| 0x85 | 133 | location | location enum | 1 = "on this computer" |
| 0x86 | 134 | icloud_status | icloud_status enum | 5 = "Local Only" |

## Operator codes

Two separate tables — string fields and numeric fields use different code
spaces for the same code value.

**String fields:**

| Code | Meaning |
|---|---|
| 1 | is / equals |
| 2 | contains |
| 4 | starts with |
| 8 | ends with |

Unconfirmed: none — all string operators are now confirmed.

**Numeric/date fields:**

| Code | Meaning |
|---|---|
| 1 | is / equals |
| 0x10 (16) | is greater than / is after |
| 0x40 (64) | is less than / is before |

Unconfirmed: whether negation combines cleanly with operators other than
"is" for every field (see Open questions).

## The "reserved2" byte (offset +3 of a rule, right after the field code)

This second byte of the two-byte "reserved2" pair switches the rule into a
different value-block interpretation:

| Value | Mode | Behaviour |
|---|---|---|
| 0 | plain value | normal single-value comparison |
| 1 | RANGE / "between" | operator byte unused; value block's bytes 0-7 = low bound, bytes 24-31 = high bound |
| 2 | RELATIVE DATE ("in the last N units") | operator byte unused; bytes 8-15 = **signed** int64 count (negative); bytes 16-23 = seconds-per-unit |
| 4 | CHECKBOX/ENUM value | operator byte unused (seen 0x00); value block is a normal single value; comparison is always "=" |

## type_flag byte (offset +1 of a rule)

| Value | Meaning |
|---|---|
| 0 | numeric/enum/date value |
| 1 | string value |
| 2 | numeric value, **negated** ("is not") |
| 3 | string value, **negated** ("is not" / "does not contain") |

Negation renders as `NOT (...)` wrapped around the whole comparison — this is
always logically correct, though it may not always match Music.app's exact
"is not" vs "does not contain" wording nuance.

**Exception:** the `ticked` field (0x1D) has only been observed with
`type_flag=2` on a plain "Ticked is true" rule, which contradicts the general
rule above (would render as `NOT (ticked = 1)` = false). The script does
**not** apply negation to this field as a targeted workaround. See Open
questions.

## Value payload shapes

- **String**: UTF-16BE text, no null terminator, length given by the 4-byte
  length field.
- **Plain numeric/date/enum** (68-byte block):
  - bytes 0-7: value (big-endian uint64)
  - bytes 8-15: zero
  - bytes 16-23: marker, always seen as 1
  - bytes 24-31: duplicate of the value
  - bytes 32-39: zero
  - bytes 40-47: marker, always seen as 1
  - bytes 48-67: zero padding
- **RANGE** (reserved2=1): same 68-byte shape, but bytes 0-7 = low bound and
  bytes 24-31 = high bound (not a duplicate).
- **RELATIVE DATE** (reserved2=2): bytes 0-7 and 24-31 hold a fixed, irrelevant
  placeholder pattern (`0x2DAE2DAE2DAE2DAE`); bytes 8-15 = signed count
  (negative); bytes 16-23 = seconds-per-unit.
- **Playlist reference**: bytes 0-7 (and the 24-31 duplicate) = an 8-byte
  Playlist Persistent ID, formatted as 16 uppercase hex digits — matches the
  `Playlist Persistent ID` key used elsewhere in Library.xml.
- **Nested group** (field code 0x00): the "value" is a complete, self-contained
  Smart Criteria blob (own 16-byte header + 120-byte preamble + own rules),
  decoded recursively.

## Date encoding

Absolute dates (`date_added`, `date_modified`, `last_played`, `last_skipped`)
are stored as **Mac absolute time**: seconds since **1904-01-01**.

## Rating bucket formula

Both `rating` and `album_rating` use a x20 scale (raw 80 = 4 stars). For a
star value N (0-5), the underlying raw value occupies the bucket:

```
[20*N, 20*N + 9]
```

- "Rating is 3 stars" → `BETWEEN 60 AND 69`
- "Rating is greater than 2 stars" → `> 49` (top of bucket 2)
- "Album Rating is in the range 2 to 3 stars" → `BETWEEN 40 AND 69` (low bound of bucket 2 to high bound of bucket 3)

## Relative-date units (seconds per unit)

| Seconds | Unit |
|---|---|
| 86400 | days |
| 604800 | weeks |
| 2628000 | months (365/12 days) |

Unconfirmed: years.

## Media Kind enum (bitmask — values are powers of two, not sequential)

| Value | Kind |
|---|---|
| 1 (2^0) | Music |
| 2097152 (2^21) | iTunes U |

Other kinds (Movies, TV Shows, Podcasts, Audiobooks, etc.) are almost
certainly other powers of two — unconfirmed.

## Location enum

| Value | Meaning |
|---|---|
| 1 | on this computer |

Other values (e.g. iCloud) unconfirmed.

## iCloud Status enum

| Value | Meaning |
|---|---|
| 5 | Local Only |

Other values (e.g. iCloud, Purchased, Matched, Uploaded) unconfirmed.

## Smart Info (limit / live-updating / match-checked-only)

16-byte header, rest of the 112-byte blob unused/zero in every example seen:

| Offset | Meaning |
|---|---|
| 2 | limit-enabled flag (0/1) |
| 3 | limit unit code (see table below) |
| 4-7 | limit "selected by" sort **dimension** code (big-endian uint32, see table below) |
| 8-11 | limit count (big-endian uint32) — only meaningful when limit is enabled; holds a leftover/default value otherwise |
| 12 | "match only checked items" flag (0/1) |
| 13 | limit sort **direction** flag (0/1) — see below |

**Limit unit codes:**

| Code | Unit |
|---|---|
| 1 | minutes |
| 2 | MB |
| 3 | items |
| 4 | hours |
| 5 | GB |

All units in the Music.app dropdown are now confirmed.

**Limit "selected by" sort dimension codes:**

| Code | Dimension |
|---|---|
| 2 | random |
| 5 | name |
| 6 | album |
| 7 | artist |
| 9 | genre |
| 21 | recently added |
| 25 | often played |
| 26 | recently played |
| 28 | rating |

**Direction flag (offset 13):** for dimensions 21, 25, and 26, this toggles
`0` = "most" / `1` = "least" (e.g. dimension 21 + direction 1 = "least
recently added"). For dimension 28 ("rating"), it toggles `0` = "highest" /
`1` = "lowest". The non-directional dimensions above (random, name, album,
artist, genre) always show direction 0 and have no "reverse" concept in the
UI.

Also observed but not confirmed/used: Smart Info offset 0 sometimes reads
0x00 rather than the usual 0x01. An earlier guess that this might mirror the
playlist's own match-all/match-any setting has since been disproven (it's
shown up as 0x00 on several "Match all" playlists too) — purpose unknown,
and unused by the script either way.

## Playlist Criteria blob structure (recap)

```
Header (16 bytes):
  0x00-0x03  magic "SLst"
  0x04-0x07  version (ignored)
  0x08-0x0B  rule count (big-endian uint32)
  0x0C-0x0F  match-any flag (0 = AND, nonzero = OR)

120-byte reserved/zero preamble (every criteria blob, every nesting depth)

Then, per rule, back-to-back:
  +0   3 bytes  reserved (zero)
  +3   1 byte   field code (0x00 = nested group sentinel)
  +4   1 byte   type_flag
  +5   2 bytes  reserved2 (second byte = mode, see table above)
  +7   1 byte   operator code
  +8   44 bytes reserved (zero)
  +52  4 bytes  value length (big-endian uint32, "L")
  +56  L bytes  value payload
```

## Open questions (as of this writing)

- **Ticked negation anomaly**: `type_flag=2` on "Ticked is true" contradicts
  the general negation model. A "Ticked is false"/"is not true" example
  would help.
- **Media Kind operator mismatch**: in two playlists ("composer", "series"),
  Media Kind's operator byte decoded as 0x00 (expected 0x01) right after a
  relative-date rule — but the same pairing decoded cleanly elsewhere
  ("Recent"). Not yet explained.
- Full Media Kind bitmask beyond Music/iTunes U.
- Full Location and iCloud Status enums beyond the one value each.
- Negation combined with operators other than "is" (currently always
  rendered as a safe, correct-but-possibly-non-idiomatic `NOT (...)` wrapper).
