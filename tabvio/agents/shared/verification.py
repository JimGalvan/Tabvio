def describe_entered_code(resume_value: object) -> str:
    """What the agent is told once a person's code has gone into the page."""
    submitted = resume_value.get("submitted") if isinstance(resume_value, dict) else None
    carry_on = "Observe the page and carry on yourself; do not ask the person to continue."
    if not submitted:
        return f"The person entered the verification code. {carry_on}"
    return (
        f"The person entered the verification code and it was submitted: {submitted}. "
        f"{carry_on}"
    )


def explain_missing_code(resume_value: object) -> str:
    """What the agent is told when the person did not supply the code it asked for."""
    reason = (
        resume_value.get("reason") if isinstance(resume_value, dict) else None
    ) or "the person did not enter one"
    return (
        f"The verification code was not entered because {reason}. "
        "Observe the page and change something first - send the code, choose "
        "another delivery method, or continue without it - before asking for a "
        "verification code again."
    )
