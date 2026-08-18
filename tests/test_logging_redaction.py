import json
import logging

from config.logging_config import JsonFormatter, redact, log_with_fields


def test_redact_masks_api_key():
    text = 'api_key="AbCdEf1234567890"'
    assert "AbCdEf1234567890" not in redact(text)
    assert "REDACTED" in redact(text)


def test_redact_masks_password_and_totp_secret():
    text = "password=mySecretPin123 totp_secret=JBSWY3DPEHPK3PXP"
    result = redact(text)
    assert "mySecretPin123" not in result
    assert "JBSWY3DPEHPK3PXP" not in result


def test_json_formatter_produces_valid_json_and_redacts(caplog):
    logger = logging.getLogger("test.redaction")
    logger.setLevel(logging.INFO)
    formatter = JsonFormatter()

    record = logger.makeRecord(
        "test.redaction", logging.INFO, __file__, 1,
        'login attempt api_key="SECRETVALUE123"', (), None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)  # must be valid JSON
    assert "SECRETVALUE123" not in output
    assert parsed["level"] == "INFO"


def test_log_with_fields_redacts_extra_fields():
    logger = logging.getLogger("test.extra")
    logger.setLevel(logging.INFO)
    formatter = JsonFormatter()
    record = logger.makeRecord(
        "test.extra", logging.INFO, __file__, 1, "session established", (), None,
        func=None,
    )
    record.extra_fields = {"jwttoken": "abcdefghijklmnop", "client_code": "A123"}
    output = formatter.format(record)
    assert "abcdefghijklmnop" not in output
    parsed = json.loads(output)
    assert parsed["client_code"] == "A123"
