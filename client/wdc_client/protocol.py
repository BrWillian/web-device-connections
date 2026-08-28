"""Message vocabulary shared with the relay.

Every error the device reports carries two fields: `error_code`, a stable slug
the dashboard can branch on and translate, and `error`, an English sentence for
logs and for UIs that have no translation for the code yet. The relay forwards
both untouched, so adding a code here is enough to make it visible in the
browser.
"""

# Inbound: relay -> device
MSG_AUTH_OK = "auth_ok"
MSG_AUTH_ERROR = "auth_error"
MSG_START_SESSION = "start_session"
MSG_INPUT = "input"
MSG_RESIZE = "resize"
MSG_FILE_BEGIN = "file_begin"
MSG_FILE_END = "file_end"
MSG_FILE_CANCEL = "file_cancel"
MSG_FILE_CHECKSUM = "file_checksum"
MSG_FILE_PULL_BEGIN = "file_pull_begin"
MSG_FILE_PULL_CANCEL = "file_pull_cancel"

# Outbound: device -> relay
MSG_OUTPUT = "output"
MSG_FILE_PUT_OK = "file_put_ok"
MSG_FILE_PUT_ERROR = "file_put_error"
MSG_FILE_PUT_PROGRESS = "file_put_progress"
MSG_FILE_PULL_INFO = "file_pull_info"
MSG_FILE_PULL_CHECKSUM = "file_pull_checksum"
MSG_FILE_PULL_END = "file_pull_end"
MSG_FILE_PULL_ERROR = "file_pull_error"

# Error codes. Stable slugs: rename one and every dashboard that branches on it
# silently stops matching, so treat these as part of the wire contract.
ERR_CANCELED = "canceled"
ERR_CHECKSUM_MISMATCH = "checksum_mismatch"
ERR_CHECKSUM_MISSING = "checksum_missing"
ERR_INTERNAL = "internal"
ERR_NOT_A_FILE = "not_a_file"
ERR_NOT_FOUND = "not_found"
ERR_NO_PATH = "no_path"
ERR_PATH_NOT_ALLOWED = "path_not_allowed"
ERR_READ_ERROR = "read_error"
ERR_SESSION_LIMIT = "session_limit"
ERR_TOO_LARGE = "too_large"
ERR_WRITE_ERROR = "write_error"

# Human-readable defaults, in English like the rest of the codebase. A caller
# may pass its own message (an errno string, say); the code stays the contract.
ERROR_MESSAGES = {
    ERR_CANCELED: "transfer canceled",
    ERR_CHECKSUM_MISMATCH: "checksum mismatch",
    ERR_CHECKSUM_MISSING: "no local checksum for this transfer",
    ERR_INTERNAL: "internal client error",
    ERR_NOT_A_FILE: "path is not a regular file",
    ERR_NOT_FOUND: "file does not exist",
    ERR_NO_PATH: "no path given",
    ERR_PATH_NOT_ALLOWED: "path is outside the allowed roots",
    ERR_READ_ERROR: "could not read the file",
    ERR_SESSION_LIMIT: "too many terminal sessions open on this device",
    ERR_TOO_LARGE: "file exceeds the size limit",
    ERR_WRITE_ERROR: "could not write the file",
}


def error(msg_type, code, message=None, **extra):
    """Build an error payload carrying both the code and a readable message."""
    payload = {
        "type": msg_type,
        "error_code": code,
        "error": message or ERROR_MESSAGES.get(code, code),
    }
    payload.update(extra)
    return payload
