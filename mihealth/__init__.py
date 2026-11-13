"""Mi Health (小米运动健康) client library.

Reverse-engineered from Mi Health 3.61.0 APK (2026-11-04 build).

Public API:
    from mihealth.auth import login_with_sms, login_with_passtoken, load_tokens
    from mihealth.client import MiHealthClient, normalize_pull_record

The body-composition history (体脂数据) is served by the fitness data endpoint:

    POST https://hlth.io.mi.com/app/v1/data/get_fitness_data_by_time
    body (encrypted form): data / rc4_hash__ / signature / _nonce
    plaintext: {"data": "{\"start_time\":...,\"end_time\":...,\"key\":\"weight\"}"}

Each row carries the complete eight-electrode report inside its ``value`` JSON.
The eco proxy (``/app/v1/eco/api_proxy`` + ``eco/scale/record/pull``) is kept as a
fallback for scale accounts / virtual members.

See README.md for the full protocol description.
"""

__version__ = "1.1.0"
