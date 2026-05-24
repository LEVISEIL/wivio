from __future__ import annotations


class VideoBotError(Exception):
    user_message = "Could not process this video."


class DownloadError(VideoBotError):
    user_message = (
        "Could not download this video. It may be private, unavailable, or region restricted."
    )


class RestrictedVideoError(DownloadError):
    user_message = "Instagram ограничил доступ к этому видео."


class FileTooLargeError(VideoBotError):
    user_message = "This video is too large for inline upload."


class UploadError(VideoBotError):
    user_message = "Could not upload this video to Telegram."


class TimeoutError(VideoBotError):
    user_message = "Video processing took too long. Try again in a moment."
