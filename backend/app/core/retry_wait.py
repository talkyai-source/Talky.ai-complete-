"""Human-readable estimates for retry messages. HTTP headers keep exact seconds."""


def wait_words(seconds: int | None) -> str:
    if seconds is None:
        return "a little while"
    if seconds < 60:
        return "less than a minute"

    minutes = (seconds + 30) // 60
    if minutes < 60:
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    hours = (minutes + 30) // 60
    return f"about {hours} hour{'s' if hours != 1 else ''}"
