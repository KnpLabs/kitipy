import kitipy


def ensure_tag_exists(kctx: kitipy.Context, tag: str):
    """Check if the given Git tag exists on both local copy and remote origin.
    This is mostly useful to ensure no invalid tag is going to be deployed.
    
    Args:
        kctx (kitipy.Context): Kitipy context.
        tag (str): Git tag to verify.
    
    Raises:
        ValueError: If the given Git tag does not exist either on local or
            remote origin.
    """
    res = kctx.local(
        'git ls-remote --exit-code --tags origin refs/tags/%s >/dev/null 2>&1'
        % (tag),
        check=False)
    if res.returncode != 0:
        kctx.fail("The given tag is not available on Git remote origin.")

    res = kctx.local(
        'git ls-remote --exit-code --tags ./. refs/tags/%s >/dev/null 2>&1' %
        (tag),
        check=False)
    if res.returncode != 0:
        kctx.fail(
            "The given tag is not available in your local Git repo. Please fetch remote tags before running this task again."
        )


def ensure_tag_is_recent(kctx: kitipy.Context, tag: str, last: int = 5):
    """Check if the given Git tag is recent enough (by default, one of the
    last five).

    Args:
        kctx (kitipy.Context): Kitipy Context.
        tag (str): Tag to look for.
    """
    res = kctx.local(
        "git for-each-ref --format='%%(refname:strip=2)' --sort=committerdate 'refs/tags/*' 2>/dev/null | tail -n%d | grep %s >/dev/null 2>&1"
        % (last, tag),
        check=False,
    )
    if res.returncode != 0:
        kctx.fail(
            'This tag seems too old: at least %d new tags have been released since %s.'
            % (last, tag))
