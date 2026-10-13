import pytest

from app.agent.validator import ValidationError, validate_sql


def test_unbalanced_quote_is_a_validation_error():
    sql = "SELECT * FROM events WHERE event_name = 'Women's Soccer'"
    with pytest.raises(ValidationError):
        validate_sql(sql, ["events"])