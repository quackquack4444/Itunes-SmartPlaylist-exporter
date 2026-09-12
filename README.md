# Itunes-SmartPlaylist-exporter
Exports iTunes Smart playlists to SQL

Built for iTunes 10.7.

Usage

    Visual dump:
    
    python3 itunes_smart_to_sql.py /path/to/Library.xml
    
    Dump to file:
    
    python3 itunes_smart_to_sql.py /path/to/Library.xml --out smart_playlists.sql
    
    Visual export of byte code
    
    python3 itunes_smart_to_sql.py /path/to/Library.xml --debug "My Playlist Name"
    
    A dump of playlists their IDs, and track counts:
    
    python3 itunes_smart_to_sql.py /path/to/Library.xml --list-playlists
    
    Optional, but messy, substitution of smaller nested sub playlists:
    
    python3 itunes_smart_to_sql.py /path/to/Library.xml --resolve-playlist-refs
