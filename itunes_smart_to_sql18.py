#!/usr/bin/env python3
"""
itunes_smart_to_sql.py

Convert the Smart Playlists in an exported iTunes / Music.app "Library.xml"
into SQL WHERE-clause fragments, assuming a `tracks` table with iTunes-style
columns (name, artist, album, genre, rating, play_count, date_added, ...).

By default, PLAYLIST-REFERENCE RULES ("Playlist is X" / "Playlist is not
X") render as `track_id IN (SELECT track_id FROM playlist_tracks WHERE
playlist_name = 'X')`, with a comment giving the persistent ID -- this
assumes a `playlist_tracks` join table, but keeps the SQL short and
readable. Pass --resolve-playlist-refs to instead inline the referenced
playlist's actual iTunes Track IDs straight from its own "Playlist Items"
list in this same Library.xml (if it has <= 500 tracks), producing a
fully self-contained, schema-agnostic `track_id IN (id1, id2, ...)` --
useful if you don't have (or don't want) a playlist_tracks table, but
verbose for large playlists.

Use --list-playlists to dump every playlist's name, Persistent ID, and
track count (tab-separated) -- handy for finding the ID behind a
"not found in this library's playlist list" comment, or just auditing
what's in the library.

--------------------------------------------------------------------------
BINARY FORMAT NOTES (read this before trusting output on unchecked rules)
--------------------------------------------------------------------------
"Smart Info" / "Smart Criteria" are undocumented binary blobs. Everything
below was reverse engineered from real --debug hex dumps across multiple
playlists on one specific Music.app library, and every offset has been
verified byte-for-byte (full buffer consumption at every nesting level,
for every example seen so far).

CRITERIA BLOB LAYOUT (top-level and nested groups both use this):

  Header (16 bytes):
    0x00-0x03  magic "SLst"
    0x04-0x07  version (ignored)
    0x08-0x0B  rule count, big-endian uint32
    0x0C-0x0F  match-any flag: 0 = AND, nonzero = OR

  Then a fixed 120-byte reserved/zero preamble (every criteria blob, at
  every nesting depth).

  Then, per rule, back-to-back:
    +0   3 bytes  reserved (zero)
    +3   1 byte   field code. 0x00 is a SENTINEL: this rule's "value" is
                  actually a nested Smart Criteria blob, decoded
                  recursively.
    +4   1 byte   type_flag. bit0 (value 1) = string value (else
                  numeric/enum/date/reference). bit1 (value 2) = this
                  rule is NEGATED ("is not" / "does not..."), confirmed
                  on a string field, inferred (not yet confirmed) to
                  generalise to numeric fields too.
    +5   2 bytes  "reserved2". Second byte, when 1, marks this rule as a
                  RANGE/"between" rule (confirmed on a numeric field) --
                  in that case the operator byte below is unused and the
                  two value slots (see below) hold the low and high
                  bounds directly rather than a duplicated single value.
    +7   1 byte   operator code (ignored when reserved2's range flag is set)
    +8   44 bytes reserved (zero in every rule seen so far)
    +52  4 bytes  value length, big-endian uint32 ("L")
    +56  L bytes  value payload -- see "VALUE PAYLOAD" below.

  VALUE PAYLOAD, by type_flag/field:
    - nested group (field code 0x00): a complete Smart Criteria blob,
      decoded recursively.
    - string (type_flag bit0 set): UTF-16BE text, no terminator.
    - plain numeric/enum/date (type_flag bit0 clear, not a range): fixed
      68-byte block --
          bytes 0-7    value, big-endian uint64
          bytes 8-15   zero
          bytes 16-23  marker, always seen as 1
          bytes 24-31  duplicate of the value
          bytes 32-39  zero
          bytes 40-47  marker, always seen as 1
          bytes 48-67  zero padding
    - RANGE numeric (range flag set): same 68-byte shape, but bytes 0-7
      are the LOW bound and bytes 24-31 are the HIGH bound (not a
      duplicate).
    - playlist reference (field code 0x28): same 68-byte shape, bytes
      0-7 (and the 24-31 duplicate) hold an 8-byte "Playlist Persistent
      ID" -- the same identifier format used elsewhere in Library.xml,
      so it can be resolved to a playlist name by cross-referencing the
      library's own playlist list.

CONFIRMED field codes (numeric ones needing the 68-byte block noted):
  0x02 name (string), 0x03 album (string), 0x04 artist (string), 0x05
  bit_rate (numeric), 0x06 sample_rate (numeric), 0x07 year (numeric),
  0x08 genre (string), 0x09 kind (string), 0x0A date_modified
  (numeric/date, same encoding as date_added), 0x0B track_number
  (numeric), 0x0C size (numeric, bytes -- confirmed from "Size is in
  the range 1 to 2 MB" -> raw 1048576/2097152), 0x0D time (numeric,
  MILLISECONDS -- confirmed from "Time is less than 20:00" -> raw value
  1200000), 0x0E comments (string), 0x10 date_added (numeric; Mac
  absolute time -- seconds since 1904-01-01), 0x12 composer (string),
  0x16 plays (numeric, play count), 0x17 last_played (date), 0x18
  disc_number (numeric), 0x19 rating (numeric, x20 scale: raw 80 = "4
  stars"), 0x1D ticked (see the NOT YET UNDERSTOOD note below -- do not
  assume the general negation rule applies to this field), 0x1F
  compilation (boolean), 0x23 bpm (numeric), 0x25 has_artwork
  (boolean), 0x27 grouping (string), 0x28 playlist reference (see
  above), 0x29 purchased (boolean), 0x36 description (string), 0x37
  category (string), 0x39 is_podcast_legacy (boolean -- NOT exposed in
  the Music.app filter UI; found only in Apple's own built-in "Recently
  Added"/"Recently Played"/"Top 25 Most Played" smart playlists, which
  all exclude podcasts via `NOT (is_podcast_legacy = TRUE)`; a real
  field that predates/parallels the Media Kind bitmask, inferred from
  context rather than a UI label), 0x3C media_kind (numeric BITMASK
  enum -- see MEDIA_KIND_ENUM note below), 0x3E programme (string,
  distinct from Sort Programme/0x53), 0x3F series (numeric), 0x44
  skips (numeric, skip count), 0x45 last_skipped (date), 0x47
  album_artist (string), 0x4E sort_name (string), 0x4F sort_album
  (string), 0x50 sort_artist (string), 0x51 sort_album_artist (string),
  0x52 sort_composer (string), 0x53 sort_programme (string), 0x5A
  album_rating (numeric, same x20 scale as rating), 0x85 location
  (numeric enum, 1 = "on this computer"; other values e.g. iCloud
  unconfirmed), 0x86 icloud_status (numeric enum, 5 = "Local Only";
  other values e.g. iCloud/Purchased/Matched/Uploaded unconfirmed).

CONFIRMED rating-bucket formula (resolves the earlier open question
  about star RANGE boundaries): star rating N occupies raw values
  [20*N, 20*N+9] -- e.g. "Rating is 3 stars" decoded as BETWEEN 60 AND
  69, and "is greater than 2 stars" decoded as a plain > 49 (the top of
  bucket 2). A RANGE rule like "2 to 3 stars" spans low=20*2=40 to
  high=20*3+9=69, matching everything seen so far exactly.

Every USER-SELECTABLE field in the Music.app filter dropdown has now
  been reverse engineered at least once. Remaining unconfirmed detail
  is at the VALUE level for a few fields (see NOT YET UNDERSTOOD below
  and the enum notes above), not at the field-identity level. There ARE
  gaps in the field code numbering (0x01, 0x0F, 0x11, 0x13-0x15,
  0x1A-0x1C, 0x1E, plus most codes near 0x38-0x3B) that have never
  appeared in any UI-built playlist -- 0x39 (is_podcast_legacy, above)
  is confirmed proof that at least some of these gaps are real,
  non-UI-exposed fields used internally by Apple's own built-in smart
  playlists, not simply unused slots. If a fresh/empty library's
  Library.xml is available, its built-in playlists (Recently Added,
  Top Rated, Top 25 Most Played, etc. -- NOT the ones using
  "Distinguished Kind" instead of "Smart Criteria", which aren't real
  smart playlists) are a good source of these, since they can't be
  recreated by hand through the criteria UI. Other confirmed gaps
  (0x13-0x15, etc.) may or may not be similarly used elsewhere -- see
  itunes_smart_playlist_reference.md for the speculation on those
  (e.g. Work/Movement Name/Movement Number for 0x13-0x15), which
  remains explicitly unconfirmed.

NOT YET UNDERSTOOD:
  - In two other playlists ("composer", "series"), Media Kind's operator
    byte also decoded as 0x00 (expected 0x01) right after a relative-date
    rule -- but that same rule/field pairing decoded cleanly (0x01) in a
    third playlist ("Recent"), so this isn't yet explained and needs its
    own --debug sample to chase down.
  - "Ticked is true" decoded with type_flag=2, which under the general
    negation-bit model (bit1=negated) would render as `NOT (ticked = 1)`
    -- directly contradicting its meaning. This script does NOT apply
    negation to the "ticked" field as a result (see Rule.to_sql), but
    that's a targeted workaround, not an understood encoding. A "Ticked
    is false" or "is not true" example would help pin down what's really
    going on -- it's possible boolean-typed fields use type_flag
    differently from string/numeric fields generally.

CONFIRMED "reserved2" second-byte modes (offset+3 of a rule, i.e. the
  byte right after the field code): 0 = plain value, 1 = RANGE/"between"
  (see above), 2 = RELATIVE DATE ("is in the last N units") -- in this
  mode, operator is unused (seen as 0x00), bytes 8-15 of the value block
  are a SIGNED int64 count (negative, e.g. -4 for "4 days"), and bytes
  16-23 are seconds-per-unit (86400 confirmed for "days", 2628000
  confirmed for "months"; weeks unconfirmed). Bytes 0-7 and 24-31 hold a
  fixed placeholder pattern in this mode and are not meaningful. 4 =
  CHECKBOX/ENUM value (confirmed on Media Kind) -- operator is unused
  (seen as 0x00) and the value payload is a normal single value; the
  comparison is always "= value" regardless of the operator byte.

CONFIRMED operator codes: 0x01 "is"/equals, 0x02 "contains", 0x04
"starts with", 0x08 "ends with", 0x40 "is less than", 0x10 "is greater
than" -- confirmed general-purpose (seen on both a date field as "is
after" and a plain numeric field, "Track Number is greater than 1"),
same code either way. All string operators are now confirmed.
Whether negation (type_flag bit1) combines cleanly with operators
other than "is" is still open -- currently rendered generically as
`NOT (...)` around the whole comparison, which is always correct but
may not match Music.app's exact "is not"/"does not contain" wording
style. Negation on a NUMERIC field (type_flag bit1 set with bit0 clear)
is CONFIRMED too (e.g. "Series is not 0" -> type_flag=2) -- EXCEPT for
"ticked", see the NOT YET UNDERSTOOD note above.

Anything not in the tables above is emitted as a commented-out hex
fragment instead of a guess. USE THE --debug FLAG on any playlist with
fields/operators you haven't checked yet before trusting its output.

SMART INFO (limit / live-updating / match-checked-only):
  offset 2    : limit-enabled flag (0/1) -- CONFIRMED
  offset 3    : limit unit code, meaningful only when limit is enabled --
                CONFIRMED values 1="minutes", 2="MB", 3="items", 4="hours",
                5="GB"; others unconfirmed. When not "items", this script
                adds a comment rather than a misleading row-count LIMIT.
  offset 4-7  : limit "selected by" sort DIMENSION code, big-endian
                uint32, meaningful only when limit is enabled -- CONFIRMED
                values 2="random", 5="name", 6="album", 7="artist",
                9="genre", 21="recently added", 25="often played",
                26="recently played", 28="rating"; others unconfirmed.
                For the last four, this is a DIMENSION shared by both
                directions -- see offset 13.
  offset 8-11 : limit count (e.g. 777), big-endian uint32 -- CONFIRMED.
                NOTE: this field holds a leftover/default value (often 25)
                even when the limit checkbox is OFF, so it must only be
                trusted/rendered when the limit-enabled flag above is set.
  offset 12   : "match only checked items" flag (0/1) -- CONFIRMED
  offset 13   : limit sort DIRECTION flag (0/1) -- CONFIRMED. For sort
                dimensions 21/25/26 this toggles "most" (0) vs "least" (1)
                (e.g. "least recently added"); for dimension 28 ("rating")
                it toggles "highest" (0) vs "lowest" (1). Irrelevant for
                non-directional dimensions (random/name/album/artist/
                genre), where it's seen as 0.
  offset 0    : NOT confirmed and not used by this script -- earlier seen
                as 0x00 on one "Match any" playlist, prompting a guess
                that it might mirror match-all/match-any, but it has
                since also shown up as 0x00 on several "Match all"
                playlists too, disproving that guess. Purpose unknown.
--------------------------------------------------------------------------

Usage:
    python3 itunes_smart_to_sql.py /path/to/Library.xml
    python3 itunes_smart_to_sql.py /path/to/Library.xml --out smart_playlists.sql
    python3 itunes_smart_to_sql.py /path/to/Library.xml --debug "My Playlist Name"
    python3 itunes_smart_to_sql.py /path/to/Library.xml --list-playlists
    python3 itunes_smart_to_sql.py /path/to/Library.xml --resolve-playlist-refs
"""

import argparse
import datetime
import plistlib
import struct
import sys
from dataclasses import dataclass, field
from typing import Optional, Union

MAGIC = b"SLst"
HEADER_LEN = 16
PREAMBLE_LEN = 120
RULE_PREFIX_LEN = 3
RULE_HEADER_LEN = 53        # field(1)+type(1)+reserved2(2)+op(1)+reserved(44)+length(4)
NUMERIC_BLOCK_LEN = 68
NESTED_GROUP_FIELD_CODE = 0x00
PLAYLIST_REF_FIELD_CODE = 0x28
MAX_INLINE_PLAYLIST_TRACK_IDS = 500
MAC_EPOCH = datetime.datetime(1904, 1, 1)

# field_code -> (sql_column_name, "string" | "numeric" | "date" | "playlist_ref")
FIELD_CODES = {
    0x02: ("name", "string"),
    0x03: ("album", "string"),
    0x04: ("artist", "string"),
    0x05: ("bit_rate", "numeric"),
    0x06: ("sample_rate", "numeric"),
    0x07: ("year", "numeric"),
    0x08: ("genre", "string"),
    0x09: ("kind", "string"),
    0x0A: ("date_modified", "date"),
    0x0B: ("track_number", "numeric"),
    0x0C: ("size", "numeric"),
    0x0D: ("time", "duration_ms"),
    0x0E: ("comments", "string"),
    0x10: ("date_added", "date"),
    0x12: ("composer", "string"),
    0x16: ("plays", "numeric"),
    0x17: ("last_played", "date"),
    0x18: ("disc_number", "numeric"),
    0x19: ("rating", "rating"),
    0x1D: ("ticked", "ticked"),
    0x1F: ("compilation", "boolean"),
    0x23: ("bpm", "numeric"),
    0x25: ("has_artwork", "boolean"),
    0x27: ("grouping", "string"),
    0x28: ("playlist_ref", "playlist_ref"),
    0x29: ("purchased", "boolean"),
    0x36: ("description", "string"),
    0x37: ("category", "string"),
    0x39: ("is_podcast_legacy", "boolean"),
    0x3C: ("media_kind", "media_kind"),
    0x3E: ("programme", "string"),
    0x3F: ("series", "numeric"),
    0x44: ("skips", "numeric"),
    0x45: ("last_skipped", "date"),
    0x47: ("album_artist", "string"),
    0x4E: ("sort_name", "string"),
    0x4F: ("sort_album", "string"),
    0x50: ("sort_artist", "string"),
    0x51: ("sort_album_artist", "string"),
    0x52: ("sort_composer", "string"),
    0x53: ("sort_programme", "string"),
    0x5A: ("album_rating", "rating"),
    0x85: ("location", "location"),
    0x86: ("icloud_status", "icloud_status"),
}

LIMIT_SORT_FIELDS = {
    2: "random",
    5: "name",
    6: "album",
    7: "artist",
    9: "genre",
    26: "recently played",
    25: "often played",
    21: "recently added",
    28: "rating",
}
# Some sort dimensions are direction-toggled by Smart Info offset 13 (0 =
# "most"/"highest", 1 = "least"/"lowest") rather than having separate codes
# for each direction -- confirmed for 21, 25, 26, 28. Codes not listed here
# have no confirmed direction concept (random/name/album/artist/genre).
LIMIT_SORT_DIRECTIONAL_PHRASING = {
    21: ("most", "least"),
    25: ("most", "least"),
    26: ("most", "least"),
    28: ("highest", "lowest"),
}

LIMIT_UNIT_CODES = {
    1: "minutes",
    2: "MB",
    3: "items",
    4: "hours",
    5: "GB",
}

OPERATOR_CODES_STRING = {
    1: "{c} = {v}",
    2: "{c} LIKE '%' || {v} || '%'",
    4: "{c} LIKE {v} || '%'",
    8: "{c} LIKE '%' || {v}",
}
OPERATOR_CODES_NUMERIC = {
    1: "{c} = {v}",
    0x40: "{c} < {v}",
    0x10: "{c} > {v}",   # confirmed general "is greater than" (dates AND plain numerics)
}

MEDIA_KIND_ENUM = {
    1: "Music",
    2: "Film",
    4: "Podcast",
    8: "Audiobook",
    32: "Music Video",
    2097152: "iTunes U",
}
# NOTE: values are powers of two (1=2^0, 2=2^1, 4=2^2, 8=2^3, 32=2^5,
# 2097152=2^21), so Media Kind is a BITMASK, not a sequential enum. Note the
# gap at 2^4=16 -- other kinds (TV Shows, Ringtones, etc.) are almost
# certainly other powers of two, unconfirmed.

LOCATION_ENUM = {
    1: "on this computer",
}
# Other values (iCloud, etc.) unconfirmed.

ICLOUD_STATUS_ENUM = {
    5: "Local Only",
}
# Other values (e.g. iCloud, Purchased, Matched, Uploaded) unconfirmed.

UNIT_SECONDS_LABELS = {
    86400: "days",
    604800: "weeks",
    2628000: "months",  # 365/12 days-worth of seconds, confirmed
}


def _decode_numeric_block(raw: bytes):
    """Return (low, high, is_confirmed_range) for a 68-byte numeric block."""
    low = int.from_bytes(raw[0:8], "big", signed=False)
    high = int.from_bytes(raw[24:32], "big", signed=False)
    return low, high


@dataclass
class Rule:
    field_code: int
    type_flag: int
    operator_code: int
    is_range: bool
    is_relative_date: bool
    is_checkbox_enum: bool
    raw_value: bytes

    @property
    def is_string(self) -> bool:
        return bool(self.type_flag & 1)

    @property
    def is_negated(self) -> bool:
        return bool(self.type_flag & 2)

    def to_sql(self, playlist_lookup: Optional[dict] = None,
               resolve_playlist_refs: bool = False) -> str:
        field_info = FIELD_CODES.get(self.field_code)
        if field_info is None:
            return (f"/* UNRECOGNISED FIELD CODE 0x{self.field_code:02X} "
                     f"(op=0x{self.operator_code:02X}, type_flag={self.type_flag}) */")

        column, value_kind = field_info
        expr = self._render_comparison(column, value_kind, playlist_lookup, resolve_playlist_refs)
        # NOTE: the "ticked" field has only been observed with type_flag=2 on
        # a plain "Ticked is true" rule, which would render as `NOT (ticked =
        # 1)` under the general negation-bit model -- directly contradicting
        # its meaning. Rather than guess, negation is not applied to this
        # field; a "Ticked is false"/"is not true" example is needed to
        # learn its real encoding.
        if self.is_negated and value_kind != "ticked":
            return f"NOT ({expr})"
        return expr

    def _render_comparison(self, column, value_kind, playlist_lookup, resolve_playlist_refs=False):
        if value_kind == "string":
            op_template = OPERATOR_CODES_STRING.get(self.operator_code)
            if op_template is None:
                return (f"/* UNRECOGNISED OPERATOR CODE 0x{self.operator_code:02X} "
                         f"for field '{column}' */")
            text = self.raw_value.decode("utf-16-be", errors="replace")
            value_sql = "'" + text.replace("'", "''") + "'"
            return op_template.format(c=column, v=value_sql)

        if len(self.raw_value) < 32:
            return f"/* value block too short for '{column}': {self.raw_value!r} */"
        low, high = _decode_numeric_block(self.raw_value)

        if self.is_relative_date:
            if len(self.raw_value) < 24:
                return f"/* relative-date value block too short for '{column}' */"
            count = int.from_bytes(self.raw_value[8:16], "big", signed=True)
            unit_seconds = int.from_bytes(self.raw_value[16:24], "big", signed=False)
            n = abs(count)
            unit_label = UNIT_SECONDS_LABELS.get(unit_seconds)
            if unit_label:
                interval = f"{n} {unit_label}"
            else:
                interval = f"{n * unit_seconds} seconds /* unrecognised unit {unit_seconds}s */"
            return f"{column} > (NOW() - INTERVAL '{interval}')"

        if self.is_range:
            # Low/high bounds are read directly; exact semantics for
            # star-rating bucket boundaries are not fully confirmed --
            # see module docstring.
            return f"{column} BETWEEN {low} AND {high}"

        op_template = (OPERATOR_CODES_NUMERIC[1] if self.is_checkbox_enum
                       else OPERATOR_CODES_NUMERIC.get(self.operator_code))
        if op_template is None:
            return (f"/* UNRECOGNISED OPERATOR CODE 0x{self.operator_code:02X} "
                     f"for field '{column}' */")

        if value_kind == "date":
            dt = MAC_EPOCH + datetime.timedelta(seconds=low)
            value_sql = f"'{dt.isoformat(sep=' ')}'"
        elif value_kind == "rating":
            stars = low / 20
            value_sql = f"{low} /* {stars:g} stars */"
        elif value_kind == "duration_ms":
            total_seconds = low // 1000
            mm, ss = divmod(total_seconds, 60)
            value_sql = f"{low} /* {mm}:{ss:02d} */"
        elif value_kind == "media_kind":
            label = MEDIA_KIND_ENUM.get(low)
            value_sql = f"{low} /* {label} */" if label else f"{low} /* unrecognised MediaKind */"
        elif value_kind == "location":
            label = LOCATION_ENUM.get(low)
            value_sql = f"{low} /* {label} */" if label else f"{low} /* unrecognised Location value */"
        elif value_kind == "icloud_status":
            label = ICLOUD_STATUS_ENUM.get(low)
            value_sql = f"{low} /* {label} */" if label else f"{low} /* unrecognised iCloud Status value */"
        elif value_kind == "ticked":
            # See the negation note in Rule.to_sql -- only "is true" (value=1,
            # type_flag=2) has been observed and confirmed for this field.
            value_sql = "TRUE" if low else "FALSE"
        elif value_kind == "boolean":
            value_sql = "TRUE" if low else "FALSE"
        elif value_kind == "playlist_ref":
            playlist_id = format(low, "016X")
            entry = (playlist_lookup or {}).get(playlist_id)
            if entry is None:
                return (f"/* playlist reference to persistent ID {playlist_id} "
                         f"-- not found in this library's playlist list */")
            name = entry["name"]
            escaped = name.replace("'", "''")
            if not resolve_playlist_refs:
                return (f"track_id IN (SELECT track_id FROM playlist_tracks "
                        f"WHERE playlist_name = '{escaped}') /* persistent ID {playlist_id} */")
            track_ids = entry["track_ids"]
            if 0 < len(track_ids) <= MAX_INLINE_PLAYLIST_TRACK_IDS:
                id_list = ", ".join(str(t) for t in track_ids)
                return (f"track_id IN ({id_list}) "
                        f"/* playlist '{escaped}', {len(track_ids)} tracks, "
                        f"resolved from Library.xml's own Track IDs */")
            if len(track_ids) > MAX_INLINE_PLAYLIST_TRACK_IDS:
                return (f"track_id IN (SELECT track_id FROM playlist_tracks "
                        f"WHERE playlist_name = '{escaped}') "
                        f"/* playlist '{escaped}' has {len(track_ids)} tracks -- "
                        f"too many to inline, falling back to an assumed "
                        f"playlist_tracks join table; persistent ID {playlist_id} */")
            return (f"track_id IN (SELECT track_id FROM playlist_tracks "
                    f"WHERE playlist_name = '{escaped}') /* playlist '{escaped}' "
                    f"has no Playlist Items in this Library.xml (empty, or items "
                    f"weren't included in the export); persistent ID {playlist_id} */")
        else:
            value_sql = str(low)

        return op_template.format(c=column, v=value_sql)


@dataclass
class Group:
    match_any: bool
    rules: list

    def to_sql(self, playlist_lookup: Optional[dict] = None,
               resolve_playlist_refs: bool = False) -> str:
        return _render_rules(self.match_any, self.rules, playlist_lookup, resolve_playlist_refs)


def _render_rules(match_any: bool, rules: list, playlist_lookup=None,
                   resolve_playlist_refs: bool = False) -> str:
    joiner = " OR " if match_any else " AND "
    parts = [r.to_sql(playlist_lookup, resolve_playlist_refs) for r in rules]
    return joiner.join(f"({p})" for p in parts)


@dataclass
class SmartPlaylist:
    name: str
    match_any: bool = False
    rules: list = field(default_factory=list)
    parse_warning: Optional[str] = None
    limit_enabled: bool = False
    limit_count: Optional[int] = None
    limit_sort_field_code: Optional[int] = None
    limit_sort_direction: int = 0
    limit_unit_code: Optional[int] = None
    match_checked_only: bool = False

    def to_sql(self, playlist_lookup: Optional[dict] = None,
               resolve_playlist_refs: bool = False) -> str:
        lines = [f"-- Smart Playlist: {self.name}"]
        if self.parse_warning:
            lines.append(f"-- WARNING: {self.parse_warning}")
        if self.match_checked_only:
            lines.append("-- NOTE: 'Match only checked items' is enabled in Music.app; "
                          "add `AND checked = TRUE` if your schema tracks that.")

        if not self.rules:
            lines.append("-- (no decodable rules -- see warning above / --debug output)")
            lines.append("SELECT * FROM tracks; -- could not build WHERE clause")
            return "\n".join(lines)

        where_clause = _render_rules(self.match_any, self.rules, playlist_lookup,
                                      resolve_playlist_refs)
        sql = f"SELECT * FROM tracks\nWHERE {where_clause}"
        if self.limit_enabled and self.limit_count is not None:
            base_label = LIMIT_SORT_FIELDS.get(self.limit_sort_field_code)
            phrasing = LIMIT_SORT_DIRECTIONAL_PHRASING.get(self.limit_sort_field_code)
            if base_label and phrasing:
                prefix = phrasing[1] if self.limit_sort_direction else phrasing[0]
                sort_label = f"{prefix} {base_label}"
            else:
                sort_label = base_label
            order_comment = (f" -- selected by: {sort_label}" if sort_label
                              else f" -- selected-by sort code 0x{self.limit_sort_field_code:02X} unrecognised")
            unit_label = LIMIT_UNIT_CODES.get(self.limit_unit_code, "items")
            if unit_label == "items":
                sql += f"\nLIMIT {self.limit_count};{order_comment}"
            else:
                sql += (f"\n-- LIMIT is measured in '{unit_label}' here (Music.app 'Limit to "
                        f"{self.limit_count} {unit_label}'), not row count -- no direct SQL "
                        f"LIMIT equivalent emitted.{order_comment}")
                sql += ";"
        else:
            sql += ";"
        lines.append(sql)
        return "\n".join(lines)


def decode_smart_criteria(blob: bytes):
    if len(blob) < HEADER_LEN or blob[0:4] != MAGIC:
        return False, [], f"missing/unexpected magic bytes (got {blob[0:4]!r})"

    rule_count = struct.unpack_from(">I", blob, 8)[0]
    match_any = bool(struct.unpack_from(">I", blob, 12)[0])

    pos = HEADER_LEN + PREAMBLE_LEN
    rules: list = []
    warning = None

    for i in range(rule_count):
        try:
            if pos + RULE_PREFIX_LEN + RULE_HEADER_LEN > len(blob):
                warning = (f"ran out of bytes before rule {i + 1}/{rule_count} "
                           f"fixed fields (pos={pos})")
                break

            pos += RULE_PREFIX_LEN
            field_code = blob[pos]
            type_flag = blob[pos + 1]
            reserved2 = blob[pos + 2:pos + 4]
            operator_code = blob[pos + 4]
            is_range = reserved2[1] == 1
            is_relative_date = reserved2[1] == 2
            is_checkbox_enum = reserved2[1] == 4
            value_len = struct.unpack_from(">I", blob, pos + 49)[0]
            pos += RULE_HEADER_LEN

            if pos + value_len > len(blob):
                warning = (f"value length ({value_len} bytes) overruns buffer "
                           f"on rule {i + 1}/{rule_count} (pos={pos})")
                break

            raw_value = blob[pos:pos + value_len]
            pos += value_len

            if field_code == NESTED_GROUP_FIELD_CODE:
                child_match_any, child_rules, child_warning = decode_smart_criteria(raw_value)
                if child_warning and warning is None:
                    warning = f"nested group (rule {i + 1}/{rule_count}): {child_warning}"
                rules.append(Group(child_match_any, child_rules))
            else:
                if not (type_flag & 1) and value_len != NUMERIC_BLOCK_LEN:
                    warning = (f"rule {i + 1}/{rule_count}: numeric value block "
                               f"was {value_len} bytes, expected {NUMERIC_BLOCK_LEN} "
                               f"-- layout may not match this rule")
                rules.append(Rule(field_code, type_flag, operator_code, is_range,
                                   is_relative_date, is_checkbox_enum, raw_value))
        except struct.error as exc:
            warning = f"struct error decoding rule {i + 1}/{rule_count}: {exc}"
            break

    if warning is None and pos != len(blob):
        warning = (f"decoded {len(rules)} rule(s) but {len(blob) - pos} trailing "
                   f"byte(s) remain unconsumed -- layout may not match this blob; "
                   f"re-check with --debug")

    if len(rules) < rule_count and warning is None:
        warning = f"only decoded {len(rules)} of {rule_count} rules"

    return match_any, rules, warning


def decode_smart_info(blob: bytes):
    """See module docstring for confirmed Smart Info offsets."""
    limit_enabled = bool(blob[2]) if len(blob) > 2 else False
    limit_unit_code = blob[3] if len(blob) > 3 else None
    sort_field_code = struct.unpack_from(">I", blob, 4)[0] if len(blob) >= 8 else None
    limit_count = struct.unpack_from(">I", blob, 8)[0] if len(blob) >= 12 else None
    match_checked_only = bool(blob[12]) if len(blob) > 12 else False
    sort_direction = blob[13] if len(blob) > 13 else 0
    return (limit_enabled, limit_unit_code, sort_field_code, limit_count,
            match_checked_only, sort_direction)


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:06X}  {hex_part:<{width * 3}}  {ascii_part}")
    return "\n".join(lines)


def build_playlist_lookup(library: dict) -> dict:
    """Map 'Playlist Persistent ID' (uppercase hex string) -> {"name":
    playlist name, "track_ids": [real iTunes Track IDs from that
    playlist's own 'Playlist Items']}, for resolving playlist-reference
    criteria (field code 0x28). Because the whole Library.xml is already
    loaded, a referenced playlist's actual members can be resolved to
    real Track ID values straight from the file -- no assumed join
    table/schema needed on the SQL side."""
    lookup = {}
    for pl in library.get("Playlists", []):
        pid = pl.get("Playlist Persistent ID")
        name = pl.get("Name")
        if not (pid and name):
            continue
        track_ids = [item["Track ID"] for item in pl.get("Playlist Items", [])
                     if "Track ID" in item]
        lookup[pid.upper()] = {"name": name, "track_ids": track_ids}
    return lookup


def load_smart_playlists(library: dict) -> list:
    results = []
    for pl in library.get("Playlists", []):
        criteria_blob = pl.get("Smart Criteria")
        if criteria_blob is None:
            continue

        name = pl.get("Name", "(unnamed playlist)")
        match_any, rules, warning = decode_smart_criteria(criteria_blob)
        (limit_enabled, limit_unit_code, sort_field_code, limit_count,
         match_checked_only, sort_direction) = decode_smart_info(pl.get("Smart Info", b""))
        results.append(SmartPlaylist(
            name=name, match_any=match_any, rules=rules, parse_warning=warning,
            limit_enabled=limit_enabled, limit_count=limit_count,
            limit_sort_field_code=sort_field_code, limit_unit_code=limit_unit_code,
            limit_sort_direction=sort_direction, match_checked_only=match_checked_only,
        ))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert iTunes/Music.app smart playlists to SQL WHERE clauses.")
    parser.add_argument("library_xml", help="Path to your exported Library.xml")
    parser.add_argument("--out", help="Write SQL output to this file instead of stdout")
    parser.add_argument("--debug", metavar="PLAYLIST_NAME",
                         help="Hex-dump the raw Smart Criteria/Info blobs for one "
                              "playlist (by exact name) instead of converting everything")
    parser.add_argument("--list-playlists", action="store_true",
                         help="List every playlist's name and Persistent ID (tab-separated) "
                              "instead of converting smart playlists")
    parser.add_argument("--resolve-playlist-refs", action="store_true",
                         help="For 'Playlist is/is not X' rules, inline the referenced "
                              "playlist's actual Track IDs (up to 500) instead of the plain "
                              "'playlist_tracks' subquery. Off by default -- the plain form "
                              "is simpler to read; this is opt-in for anyone who wants a "
                              "self-contained WHERE clause with no assumed schema.")
    args = parser.parse_args()

    try:
        with open(args.library_xml, "rb") as f:
            library = plistlib.load(f)
    except Exception as exc:
        print(f"Could not read/parse {args.library_xml}: {exc}", file=sys.stderr)
        return 1

    if args.debug:
        for pl in library.get("Playlists", []):
            if pl.get("Name") == args.debug and "Smart Criteria" in pl:
                print(f"=== Smart Info ({len(pl.get('Smart Info', b''))} bytes) ===")
                print(hexdump(pl.get("Smart Info", b"")))
                print(f"\n=== Smart Criteria ({len(pl['Smart Criteria'])} bytes) ===")
                print(hexdump(pl["Smart Criteria"]))
                return 0
        print(f"No smart playlist named {args.debug!r} found.", file=sys.stderr)
        return 1

    if args.list_playlists:
        lines = ["Name\tPersistent ID\tTrack Count"]
        for pl in library.get("Playlists", []):
            name = pl.get("Name", "(unnamed playlist)")
            pid = pl.get("Playlist Persistent ID", "(none)")
            track_count = len(pl.get("Playlist Items", []))
            lines.append(f"{name}\t{pid}\t{track_count}")
        output_text = "\n".join(lines)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(output_text)
            print(f"Wrote {len(lines) - 1} playlist(s) to {args.out}")
        else:
            print(output_text)
        return 0

    playlist_lookup = build_playlist_lookup(library)
    smart_playlists = load_smart_playlists(library)

    if not smart_playlists:
        print("No smart playlists found in that library file.", file=sys.stderr)
        return 0

    output_lines = []
    for pl in smart_playlists:
        output_lines.append(pl.to_sql(playlist_lookup, args.resolve_playlist_refs))
        output_lines.append("")

    output_text = "\n".join(output_lines)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output_text)
        print(f"Wrote {len(smart_playlists)} smart playlist(s) to {args.out}")
    else:
        print(output_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
