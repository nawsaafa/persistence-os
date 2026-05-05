"""Phase 2.1c — property-based totality tests for validate_attrs (Design §10.6)."""
from hypothesis import given, settings, strategies as st

from persistence.claim._registry import CLAIM_KINDS
from persistence.claim._validate import (
    ClaimValidationError,
    UnknownClaimKindError,
    validate_attrs,
)


@given(
    kind=st.text(min_size=0, max_size=50),
    attrs=st.dictionaries(
        keys=st.text(min_size=0, max_size=20),
        values=st.one_of(
            st.text(), st.integers(), st.booleans(), st.none(),
            st.lists(st.integers(), max_size=3),
        ),
        max_size=10,
    ),
)
@settings(max_examples=200)
def test_validate_attrs_is_total(kind, attrs):
    """For any (kind, attrs), validate_attrs either returns valid attrs OR
    raises a documented exception. Never returns silently invalid output,
    never raises an undocumented exception.
    """
    try:
        out = validate_attrs(kind, attrs)
        # If we got here, kind must be a claim kind AND attrs must be valid
        assert kind in CLAIM_KINDS
        assert isinstance(out, dict)
    except UnknownClaimKindError:
        assert kind not in CLAIM_KINDS
    except ClaimValidationError:
        # acceptable — schema or invariant violation
        pass
    # any other exception type is a totality bug — the test will fail with an unhandled exception
