"""Single release version; changing this on main triggers a tested release."""
APP_NAME = 'Cosmere Video Recording Suite'
VERSION = '0.5.0-alpha26'
RELEASE_NOTES = 'Cached FFmpeg discovery removes repeated executable searches during video drawing. Compact editable capture replaces the oversized lossless color stream with compressed video and a lossless transparency mask.\nPlugin communication deadlines now include lock contention and pipe writes. Failed recordings preserve recoverable audio alongside video.\nRecording diagnostics include capture size and automatic thread snapshots when encoder or audio work stalls. Stop, Discard and approved baseline/advanced exports are preserved.'
