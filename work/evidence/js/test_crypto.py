import json, sys, base64, re, time
sys.path.insert(0, r"D:\workspace\github\mi-fits\work\lead")
from mihealth_client import (encrypt_parameters, decrypt_response, sign_params,
                             derive_key, make_nonce, RC4, qs_stringify)

vec = json.load(open(r"D:\workspace\github\mi-fits\work\lead\js_vectors.json", encoding="utf-8-sig"))
NONCE = "AAAAAAAAAAAAAAAA"
SSEC = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
fa = {}

payload = {"eco_api": "eco/scale/record/pull",
           "params": json.dumps({"uid": 1987654321, "did": "0", "pageSize": 200,
                                 "createTime": 1731234567890}, separators=(",", ":"))}
srd = {"data": json.dumps(payload, separators=(",", ":"), ensure_ascii=False)}
exp_srd = vec["stringifyRequestData"]
fa["stringifyRequestData"] = (srd == exp_srd, "got=%r\nexp=%r" % (srd, exp_srd))

got = encrypt_parameters(srd, "/app/v1/eco/api_proxy", "post", NONCE, SSEC)
exp = vec["encrypted"]
fa["encrypt_parameters"] = (got == exp, "got=%r\nexp=%r" % (got, exp))

dec = decrypt_response(exp["data"], NONCE, SSEC)
fa["decrypt_data"] = (dec == srd["data"], "got=%r" % (dec,))

dec2 = decrypt_response(encrypt_parameters({"data": "hello 世界"}, "/app/v1/eco/api_proxy", "post", NONCE, SSEC)["data"], NONCE, SSEC)
fa["decrypt_utf8"] = (dec2 == "hello 世界", "got=%r" % (dec2,))

form = qs_stringify(got)
fa["form"] = (form == vec["form"], "" if form == vec["form"] else "got=%s\nexp=%s" % (form, vec["form"]))

srv = 1794900000.0
n = make_nonce(srv)
raw = base64.b64decode(n)
fa["nonce_len"] = (len(raw) == 12, "len=%d" % len(raw))
fa["nonce_tail"] = (int.from_bytes(raw[8:12], "big") == int(srv // 60), "tail=%d" % int.from_bytes(raw[8:12], "big"))

to_camel = lambda k: re.sub(r"_([a-z])", lambda m: m.group(1).upper(), k)
to_snake = lambda k: re.sub(r"[A-Z]", lambda m: "_" + m.group(0).lower(), k)
fa["camel"] = (to_camel("rc4_hash_") == "rc4Hash_", to_camel("rc4_hash_"))
fa["snake"] = (to_snake("aB") == "a_b", to_snake("aB"))

ok = True
for k, (passed, detail) in fa.items():
    print(("PASS " if passed else "FAIL ") + k + ("" if passed else "  " + str(detail)))
    ok = ok and passed
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)

