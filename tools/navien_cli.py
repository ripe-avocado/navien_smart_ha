#!/usr/bin/env python3
"""나비엔 스마트 조회용 CLI.

이 도구는 **읽기 전용**이다. 기기에 제어 명령을 보내지 않는다.

  python3 tools/navien_cli.py login
  python3 tools/navien_cli.py devices
  python3 tools/navien_cli.py devices --raw

표준 라이브러리만 쓴다. 추가 설치가 필요 없다.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import http.cookiejar
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

API_URL = "https://nskr.naviensmartcontrol.com/api/v2.0"
LOGIN_URL = "https://member.naviensmartcontrol.com"
# 앱의 `Constants.getIotEndPoint()` 값. AWS IoT 앞단의 나비엔 자체 도메인이다.
# dev: nskr-dev-iot… / qa: nskr-stg-iot…
IOT_ENDPOINT = "nskr-iot.naviensmartcontrol.com"
IOT_REGION = "ap-northeast-2"
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 APP_NAVIENSMART_IOS"
)

ROOT = Path(__file__).resolve().parent.parent
SESSION_FILE = ROOT / ".navien_cli.json"
ENV_FILE = ROOT / ".env"

SERVICE_NAMES = {100: "보일러", 200: "숙면매트", 300: "환기청정(에어원)", 500: "스마트홈"}
MODEL_TYPES = {"em": "카본", "wm": "온수", "fm": "사계절"}

# notes/api-spec.md 의 매트 modelCode 표. 진단 표시용이며 동작에 쓰지 않는다.
MATE_MODELS = {
    "1": "EQM572", "2": "EQM580", "3": "EQM581", "4": "EQM582/590",
    "5": "EQM591", "6": "EQM650", "17": "EMW700/720", "18": "EMW721",
    "257": "EME500/501", "258": "EME520/521", "259": "EME550S",
    "260": "EME551D", "261": "EME650D", "262": "EME651P",
    "514": "EMW750", "530": "EMF500",
}


class NavienError(Exception):
    pass


# ---------------------------------------------------------------- HTTP


def _ssl_context() -> ssl.SSLContext:
    """인증서 저장소를 찾는다.

    python.org 배포판 파이썬은 macOS 시스템 인증서를 못 읽는 경우가 있다
    (`CERTIFICATE_VERIFY_FAILED`). certifi → /etc/ssl/cert.pem → 기본값 순으로
    시도한다. **검증을 끄지는 않는다.**
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    if Path("/etc/ssl/cert.pem").exists():
        try:
            return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
        except Exception:
            pass
    return ssl.create_default_context()


SSL_CONTEXT = _ssl_context()


def _opener() -> urllib.request.OpenerDirector:
    """쿠키를 물고 리다이렉트를 따라가는 opener. 로그인 흐름에 필요하다."""
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=SSL_CONTEXT),
    )


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    send = (opener or _opener()).open
    try:
        with send(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise NavienError(
                "TLS 인증서 검증에 실패했다. 파이썬이 인증서 저장소를 못 찾는 상태다.\n"
                "  해결: python3 -m pip install --upgrade certifi\n"
                "  (검증을 끄는 우회는 하지 않는다)"
            ) from exc
        raise NavienError(f"{url} 접속 실패: {reason}") from exc


def _api(method: str, path: str, token: str, *, query: dict[str, Any] | None = None,
         payload: dict[str, Any] | None = None,
         raw_body: str | None = None) -> dict[str, Any]:
    """`raw_body` 를 주면 그 문자열을 그대로 보낸다.

    제어 요청은 `topic` 의 '/' 이스케이프를 앱과 똑같이 맞춰야 해서 원문이 필요하다.
    """
    url = f"{API_URL}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers = {"Authorization": token}
    body = None
    if raw_body is not None:
        headers["Content-Type"] = "application/json"
        body = raw_body.encode()
    elif payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()

    status, raw = _request(method, url, headers=headers, body=body)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise NavienError(f"{path} 응답이 JSON 이 아니다 (HTTP {status}): {raw[:200]!r}")

    code = data.get("code")
    if code == 200:
        return data
    hints = {
        400: "잘못된 요청",
        404: "인증 실패 — 다른 기기에서 로그인했을 수 있다",
        407: "토큰 만료 — login 을 다시 실행한다",
    }
    hint = hints.get(code, data.get("msg", ""))
    raise NavienError(f"{path} 실패 (code={code}): {hint}")


# ---------------------------------------------------------------- 인증


def _read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _credentials(args: argparse.Namespace) -> tuple[str, str]:
    env = _read_env()
    username = (
        args.username
        or os.environ.get("NAVIEN_USERNAME")
        or env.get("NAVIEN_USERNAME")
        or input("나비엔 아이디: ").strip()
    )
    password = (
        os.environ.get("NAVIEN_PASSWORD")
        or env.get("NAVIEN_PASSWORD")
        or getpass.getpass("비밀번호 (화면에 표시되지 않음): ")
    )
    if not username or not password:
        raise NavienError("아이디와 비밀번호가 필요하다.")
    return username, password


def login(username: str, password: str) -> dict[str, Any]:
    """1단계 — 폼 로그인. 쿠키를 물고 리다이렉트를 따라간다."""
    opener = _opener()
    body = urllib.parse.urlencode({"username": username, "password": password}).encode()
    status, raw = _request(
        "POST",
        f"{LOGIN_URL}/member/login",
        headers={
            "Origin": LOGIN_URL,
            "Referer": f"{LOGIN_URL}/member/login",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body=body,
        opener=opener,
    )
    html = raw.decode("utf-8", errors="replace")

    if 'id="loginFailPopup" style="display:none;"' in html:
        if "입력한 정보가 일치하지 않습니다." not in html:
            raise NavienError("아이디가 틀렸다.")
        match = re.search(r"현재 (\d)회", html)
        if match:
            raise NavienError(
                f"비밀번호가 틀렸다. 5회 실패하면 비밀번호를 재설정해야 한다 (현재 {match.group(1)}회)."
            )
        raise NavienError("비밀번호가 틀렸다. 비밀번호 재설정이 필요할 수 있다.")

    if "passwordChg" in html:
        raise NavienError(
            "서버가 비밀번호 변경을 요구한다. 앱이나 웹에서 먼저 처리한 뒤 다시 시도한다.\n"
            "  (앱은 '나중에 변경' 을 눌러 미룰 수 있다. 이 CLI 는 계정 상태를 바꾸지 않는다.)"
        )

    lines = [line for line in html.splitlines() if "var message = " in line]
    if not lines:
        raise NavienError(f"로그인 응답에서 토큰을 찾지 못했다 (HTTP {status}).")
    line = lines[0]
    payload = line[line.index("{"): line.rindex("}") + 1]
    return json.loads(payload)


def secured_sign_in(access_token: str, user_id: str, account_seq: int) -> dict[str, Any]:
    """2단계 — 토큰 로그인. home 목록과 AWS IoT 임시 자격증명이 여기서 나온다."""
    data = _api(
        "POST",
        "/users/secured-sign-in",
        access_token,
        payload={"userId": user_id, "accountSeq": account_seq},
    )
    if not data.get("data"):
        raise NavienError("secured-sign-in 응답에 data 가 없다.")
    return data["data"]


def cmd_login(args: argparse.Namespace) -> int:
    username, password = _credentials(args)

    print("1/2 로그인 …")
    res = login(username, password)
    access_token = res["accessToken"]
    user_id = res["loginId"]
    account_seq = res["userSeq"]

    print("2/2 토큰 로그인 …")
    data = secured_sign_in(access_token, user_id, account_seq)

    homes = data.get("home") or []
    if not homes:
        raise NavienError("등록된 home 이 없다.")
    user_seq = data["userInfo"]["userSeq"]

    session = {
        "accessToken": access_token,
        "refreshToken": res.get("refreshToken"),
        "userId": user_id,
        "accountSeq": account_seq,
        "userSeq": user_seq,
        "homes": [{"homeSeq": h.get("homeSeq"), "nickname": h.get("nickname"),
                   "devices": h.get("devices") or []} for h in homes],
        "awsAuthInfoPresent": bool(data.get("authInfo")),
    }
    auth = data.get("authInfo") or {}
    if auth:
        # AWS IoT 접속용 임시 자격증명. watch 에서 쓴다.
        session["aws"] = {
            "accessKeyId": auth.get("accessKeyId"),
            "secretKey": auth.get("secretKey"),
            "sessionToken": auth.get("sessionToken"),
            "expiresIn": auth.get("authorizationExpiresIn"),
        }
    # IoT 엔드포인트는 기기 응답에 실려 온다. 상수로 박지 않는다.
    try:
        devs = _api("GET", "/devices", access_token,
                    query={"homeSeq": homes[0]["homeSeq"], "userSeq": user_seq})
        for dev in (_dig(devs, "data", "devices") or []):
            ep = _dig(dev, "Properties", "registry", "attributes",
                      "network", "server", "endpoint")
            if ep:
                session["iotEndpoint"] = ep
                break
    except NavienError:
        pass
    SESSION_FILE.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    SESSION_FILE.chmod(0o600)

    print()
    print("로그인 성공.")
    print(f"  userSeq      : {user_seq}")
    print(f"  accountSeq   : {account_seq}")
    for home in session["homes"]:
        print(f"  home         : {home['homeSeq']}  \"{home['nickname']}\"  "
              f"기기 {len(home['devices'])}대")
    print(f"  AWS IoT 자격증명: {'받았음' if session['awsAuthInfoPresent'] else '없음'}")
    print()
    print(f"세션 저장: {SESSION_FILE.name} (권한 600, git 에 올라가지 않음)")
    print("다음: python3 tools/navien_cli.py devices")
    return 0


def _load_session() -> dict[str, Any]:
    if not SESSION_FILE.exists():
        raise NavienError("세션이 없다. 먼저 login 을 실행한다.")
    return json.loads(SESSION_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 기기 조회


def _mask(value: Any) -> str:
    """식별자를 가린다. 같은 값은 같게 표시되므로 대조는 된다."""
    if value in (None, ""):
        return "-"
    text = str(value)
    digest = hashlib.sha256(text.encode()).hexdigest()[:6]
    return f"<{digest}:{len(text)}>"


def _dig(obj: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    return str(value)


def _print_device(dev: dict[str, Any], redact: bool) -> None:
    ident = _mask if redact else (lambda v: _fmt(v))

    service = dev.get("serviceCode")
    model_code = dev.get("modelCode")
    attrs = _dig(dev, "Properties", "registry", "attributes") or {}
    functions = attrs.get("functions") or {}
    mcu = attrs.get("mcu") or {}
    model_type = attrs.get("modelType")

    print(f"── deviceSeq {dev.get('deviceSeq')} " + "─" * 46)
    print(f"  종류         : {service} ({SERVICE_NAMES.get(service, '미확인')})")
    print(f"  modelName    : {_fmt(dev.get('modelName'))}")

    line = f"  modelCode    : {_fmt(model_code)}"
    if service == 200 and str(model_code) in MATE_MODELS:
        line += f"   → {MATE_MODELS[str(model_code)]} (표 대조)"
    elif service == 200:
        line += "   → 표에 없는 모델 (신규일 수 있음)"
    print(line)

    if model_type:
        print(f"  modelType    : {model_type} ({MODEL_TYPES.get(model_type, '미확인')})")
    print(f"  연결 상태    : {_fmt(dev.get('connected'))}  (0 = 오프라인)")
    print(f"  deviceId     : {ident(dev.get('deviceId'))}")
    print(f"  mqttTopicKey : {ident(dev.get('mqttTopicKey'))}")

    nick = _dig(dev, "Properties", "nickName") or {}
    side = nick.get("side") or {}
    if side:
        print(f"  별칭         : {_fmt(nick.get('mainItem'))} "
              f"(좌 {_fmt(side.get('left'))} / 우 {_fmt(side.get('right'))})")
    elif nick:
        print(f"  별칭         : {_fmt(nick.get('mainItem'))}")

    if mcu:
        print(f"  mcu          : matType={_fmt(mcu.get('matType'))} "
              f"modelCode={_fmt(mcu.get('modelCode'))} "
              f"capacity={_fmt(mcu.get('capacity'))} "
              f"country={_fmt(mcu.get('countryCode'))}")

    if not functions:
        print("  functions    : 없음 (이 serviceCode 는 구조가 다를 수 있다)")
        print()
        return

    print("  ── functions (서버가 내려준 기능 메타데이터)")
    for label, key in (("전원 제어", "powerCtrl"), ("잠금", "lockMode"), ("절전", "powerSaving")):
        if key in functions:
            print(f"     {label:<10}: {_fmt(functions.get(key))}")

    for label, key in (("heatControl", "heatControl"), ("coolControl", "coolControl")):
        ctl = functions.get(key)
        if not isinstance(ctl, dict):
            continue
        unit = ctl.get("unit")
        axis = {"1.0L": "단계 (level, 정수, 1 간격)",
                "0.5C": "온도 (temperature, 실수, 0.5°C 간격)"}.get(unit)
        print(f"     {label}:")
        print(f"        unit      : {_fmt(unit)}"
              + (f"   → {axis}" if axis else "   → ★ 처음 보는 값. 제어 축 미확인"))
        print(f"        범위      : {_fmt(ctl.get('rangeMin'))} ~ {_fmt(ctl.get('rangeMax'))}")
        print(f"        safeValue : {_fmt(ctl.get('safeValue'))} "
              f"(enableSafe={_fmt(ctl.get('enableSafe'))})")

    sleep = functions.get("sleepMode")
    if isinstance(sleep, dict):
        print("     sleepMode:")
        print(f"        enable    : {_fmt(sleep.get('enable'))}   "
              f"sensor={_fmt(sleep.get('sensor'))}   Kaist={_fmt(sleep.get('Kaist'))}")
        print(f"        범위      : {_fmt(sleep.get('rangeMin'))} ~ {_fmt(sleep.get('rangeMax'))}")
        print(f"        durations : {_fmt(sleep.get('durations'))}")
        print(f"        exitAlarm : {_fmt(sleep.get('exitAlarm'))}")

    sched = functions.get("schedule")
    if isinstance(sched, dict):
        print(f"     schedule   : 1회={_fmt(sched.get('oneTime'))} "
              f"주간={_fmt(sched.get('weekly'))} 개인맞춤={_fmt(sched.get('personal'))}")

    extra = set(functions) - {
        "powerCtrl", "lockMode", "powerSaving", "heatControl",
        "coolControl", "sleepMode", "schedule",
    }
    if extra:
        print(f"     ★ 표에 없는 functions 키: {sorted(extra)}")

    net = attrs.get("network") or {}
    wifi = attrs.get("wifi") or {}
    if net or wifi:
        print("  ── 네트워크")
        print(f"     localIp    : {ident(wifi.get('localIp'))}")
        print(f"     AP ssid    : {ident(_dig(net, 'accessPoint', 'ssid'))}")
        print(f"     AP mac     : {ident(_dig(net, 'accessPoint', 'mac'))}")
        print(f"     endpoint   : {_fmt(_dig(net, 'server', 'endpoint'))}")
    print()


def cmd_devices(args: argparse.Namespace) -> int:
    session = _load_session()
    homes = session["homes"]
    home_seq = args.home_seq or homes[0]["homeSeq"]

    data = _api(
        "GET",
        "/devices",
        session["accessToken"],
        query={"homeSeq": home_seq, "userSeq": session["userSeq"]},
    )
    devices = _dig(data, "data", "devices") or []

    if args.raw:
        print(json.dumps(devices, ensure_ascii=False, indent=2))
        return 0

    print(f"home {home_seq} — 기기 {len(devices)}대")
    print()
    for dev in devices:
        _print_device(dev, redact=not args.no_redact)

    if not args.no_redact:
        print("기기ID·IP·MAC·SSID 는 가려서 출력했다. 원본이 필요하면 --no-redact 를 붙인다.")
    print("전체 JSON: --raw  (공유 전에 식별자를 지운다)")
    return 0


def _find_device(token: str, home_seq: int, user_seq: int, device_seq: int) -> dict[str, Any]:
    data = _api("GET", "/devices", token, query={"homeSeq": home_seq, "userSeq": user_seq})
    for dev in (_dig(data, "data", "devices") or []):
        if dev.get("deviceSeq") == device_seq:
            return dev
    raise NavienError(f"deviceSeq {device_seq} 를 기기 목록에서 찾지 못했다.")


def cmd_control(args: argparse.Namespace) -> int:
    """단계(level) 제어 명령을 보낸다. **기기가 실제로 반응한다.**

    앱의 `control-temp` 페이로드를 그대로 따른다 — `event` + `heater` 만 채운다.
    """
    session = _load_session()
    home_seq = args.home_seq or session["homes"][0]["homeSeq"]
    user_seq = session["userSeq"]
    token = session["accessToken"]

    dev = _find_device(token, home_seq, user_seq, args.device_seq)
    attrs = _dig(dev, "Properties", "registry", "attributes") or {}
    heat = _dig(attrs, "functions", "heatControl") or {}
    unit = heat.get("unit")
    device_id = dev["deviceId"]
    model_code = dev["modelCode"]
    nickname = _dig(dev, "Properties", "nickName", "mainItem")

    if unit != "1.0L":
        raise NavienError(
            f"이 기기의 heatControl.unit 은 '{unit}' 이다. 이 명령은 단계형('1.0L') 전용이다.\n"
            "  값 체계를 모르는 기기에 추측으로 명령을 보내지 않는다."
        )

    zones = {k: v for k, v in
             (("single", args.single), ("left", args.left), ("right", args.right))
             if v is not None}
    if not zones:
        raise NavienError("--single / --left / --right 중 최소 하나에 단계를 지정한다.")

    lo, hi = heat.get("rangeMin"), heat.get("rangeMax")
    for zone, level in zones.items():
        if lo is not None and hi is not None and not (lo <= level <= hi):
            raise NavienError(f"{zone} 단계 {level} 는 서버가 알려준 범위 {lo}~{hi} 를 벗어난다.")

    heater = {z: {"enable": True, "level": {"set": int(v)}} for z, v in zones.items()}
    topic = f"$aws/things/{device_id}/shadow/name/status/update"
    payload = {
        "serviceCode": dev["serviceCode"],
        "topic": "@@TOPIC@@",
        "payload": {"state": {"desired": {
            "event": {"modelCode": int(model_code)},
            "heater": heater,
        }}},
    }
    # 앱은 topic 의 '/' 를 '\/' 로 이스케이프해 보낸다. 서버가 까다로울 수 있어 그대로 맞춘다.
    body = json.dumps(payload, ensure_ascii=False).replace(
        '"@@TOPIC@@"', json.dumps(topic, ensure_ascii=False).replace("/", "\\/")
    )

    safe = heat.get("safeValue")
    print(f"대상   : deviceSeq {args.device_seq}  \"{nickname}\"  {dev.get('modelName')}")
    print(f"unit   : {unit}  (범위 {lo}~{hi}, 고온경고선 {safe})")
    print(f"연결   : {dev.get('connected')}")
    print(f"설정   : " + ", ".join(f"{z}={v}단계" for z, v in zones.items()))
    if safe is not None and any(v > safe for v in zones.values()):
        print(f"  ※ 고온경고선({safe}) 을 넘는 값이 있다. 앱에서도 경고 표시가 뜨는 구간이다.")
    print()
    print("보낼 본문:")
    print("  " + body)
    print()

    if not args.yes:
        print("실제로 보내려면 --yes 를 붙인다. 아무것도 보내지 않고 종료한다.")
        return 0

    if not dev.get("connected"):
        print("경고: 기기가 오프라인이다(connected=0). 명령은 shadow 에만 쌓인다.")

    res = _api("POST", f"/devices/{args.device_seq}/control", token,
               query={"homeSeq": home_seq, "userSeq": user_seq},
               raw_body=body)
    print(f"전송 완료. code={res.get('code')} msg={res.get('msg')}")
    print("shadow 반영은 watch 로 확인한다. 이 명령은 재전송하지 않는다.")
    return 0


def _sigv4_encode(value: Any) -> str:
    """SigV4 정규화용 인코딩. unreserved 문자만 남긴다 ('/' 도 %2F 로)."""
    return urllib.parse.quote(str(value), safe="-_.~")


def _sigv4_derive_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    import hmac

    key = f"AWS4{secret}".encode()
    for part in (datestamp, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


def describe_iot_endpoint(region: str, creds: dict[str, str]) -> str:
    """AWS IoT `DescribeEndpoint` 로 계정의 데이터 엔드포인트를 받는다.

    앱도 이렇게 한다 (dex 에 `DescribeEndpoint` 심볼 존재).
    **기기 registry 의 `network.server.endpoint` 는 기기가 접속하는 쪽이라 다르다.**
    그걸로 붙으면 `SNI ... not associated with this account` 로 403 이 난다.
    """
    import datetime
    import hmac

    service = "iot"
    host = f"iot.{region}.amazonaws.com"
    now = datetime.datetime.now(datetime.timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{region}/{service}/aws4_request"

    canonical_query = f"endpointType={_sigv4_encode('iot:Data-ATS')}"
    token = creds["sessionToken"]
    headers = {
        "host": host,
        "x-amz-date": amzdate,
        "x-amz-security-token": token,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    empty_hash = hashlib.sha256(b"").hexdigest()
    canonical_request = "\n".join(
        ["GET", "/endpoint", canonical_query, canonical_headers, signed_headers, empty_hash]
    )
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amzdate, scope,
         hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    key = _sigv4_derive_key(creds["secretKey"], datestamp, region, service)
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    status, raw = _request(
        "GET",
        f"https://{host}/endpoint?{canonical_query}",
        headers={
            "x-amz-date": amzdate,
            "x-amz-security-token": token,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={creds['accessKeyId']}/{scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        },
    )
    if status != 200:
        raise NavienError(f"DescribeEndpoint 실패 (HTTP {status}): {raw[:300]!r}")
    address = json.loads(raw).get("endpointAddress")
    if not address:
        raise NavienError(f"DescribeEndpoint 응답에 endpointAddress 가 없다: {raw[:300]!r}")
    return address


def _sigv4_ws_path(endpoint: str, region: str, creds: dict[str, str]) -> str:
    """AWS IoT WebSocket 용 SigV4 사전서명 경로를 만든다.

    보안 토큰은 **서명 계산 뒤에** 붙인다. AWS IoT 규칙이다.
    """
    import datetime
    import hmac

    service = "iotdevicegateway"
    now = datetime.datetime.now(datetime.timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{region}/{service}/aws4_request"

    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{creds['accessKeyId']}/{scope}",
        "X-Amz-Date": amzdate,
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(
        f"{_sigv4_encode(k)}={_sigv4_encode(v)}" for k, v in sorted(query.items())
    )
    empty_hash = hashlib.sha256(b"").hexdigest()
    canonical_request = "\n".join(
        ["GET", "/mqtt", canonical_query, f"host:{endpoint}\n", "host", empty_hash]
    )
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amzdate, scope,
         hashlib.sha256(canonical_request.encode()).hexdigest()]
    )

    key = _sigv4_derive_key(creds["secretKey"], datestamp, region, service)
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    token = urllib.parse.quote(creds["sessionToken"], safe="")
    return f"/mqtt?{canonical_query}&X-Amz-Signature={signature}&X-Amz-Security-Token={token}"


def cmd_watch(args: argparse.Namespace) -> int:
    """기기가 스스로 올리는 shadow 보고를 구독한다. **구독만 한다 — 발행하지 않는다.**"""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        raise NavienError(
            "watch 에는 MQTT 클라이언트가 필요하다.\n"
            "  설치: python3 -m pip install paho-mqtt\n"
            "  (login·devices 는 설치 없이 동작한다)"
        )

    import uuid

    session = _load_session()
    creds = session.get("aws")
    if not creds:
        raise NavienError("AWS 자격증명이 세션에 없다. login 을 다시 실행한다.")

    region = args.region
    endpoint = args.endpoint or IOT_ENDPOINT

    home_seq = args.home_seq or session["homes"][0]["homeSeq"]
    topic = f"{home_seq}/{args.prefix}/+"
    client_id = f"{uuid.uuid4()}-U{session['userSeq']}"

    print(f"엔드포인트 : {endpoint}")
    print(f"리전       : {region}")
    print(f"구독 토픽  : {topic}")
    print(f"clientId   : {client_id}")
    print()
    print("구독만 한다. 제어 명령을 보내지 않는다.")
    print("→ 이제 매트의 리모컨·본체 버튼으로 단계를 바꿔보세요.")
    print("   이벤트가 찍히면 기기가 스스로 서버에 올리는 것이다. Ctrl+C 로 종료.")
    print()

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=client_id, transport="websockets")
        v2 = True
    except AttributeError:  # paho-mqtt 1.x
        client = mqtt.Client(client_id=client_id, transport="websockets")
        v2 = False

    seen = {"count": 0}

    def on_connect(client, userdata, flags, reason_code, properties=None):
        code = getattr(reason_code, "value", reason_code)
        if code != 0:
            print(f"접속 거부 (code={code})")
            return
        print("접속됨. 구독 시작.")
        client.subscribe(topic, qos=0)

    def on_message(client, userdata, msg):
        seen["count"] += 1
        print(f"── 이벤트 #{seen['count']}  topic={msg.topic}")
        try:
            data = json.loads(msg.payload)
        except json.JSONDecodeError:
            print(f"   (JSON 아님) {msg.payload[:200]!r}")
            return
        inner = data.get("topic")
        if inner:
            print(f"   shadow topic : {inner}")
        state = _dig(data, "payload", "state") or {}
        for which in ("reported", "desired"):
            if which in state:
                print(f"   {which}:")
                print("     " + json.dumps(state[which], ensure_ascii=False, indent=2)
                      .replace("\n", "\n     "))
        if not state:
            print("   " + json.dumps(data, ensure_ascii=False)[:600])
        print()

    if v2:
        client.on_connect = on_connect
    else:
        client.on_connect = lambda c, u, f, rc: on_connect(c, u, f, rc)
    client.on_message = on_message

    client.tls_set_context(SSL_CONTEXT)
    client.ws_set_options(path=_sigv4_ws_path(endpoint, region, creds))

    def summary() -> None:
        print(f"\n종료. 받은 이벤트 {seen['count']}건.")
        if seen["count"] == 0:
            print("0건이면 셋 중 하나다:")
            print("  - 기기가 스스로 올리지 않는다")
            print(f"  - 토픽 접두사가 다르다 (현재 '{args.prefix}', --prefix 로 바꿔본다)")
            print("  - 그 사이 아무 변화가 없었다 (기기를 조작해야 이벤트가 난다)")

    try:
        client.connect(endpoint, 443, keepalive=60)
    except Exception as exc:
        raise NavienError(f"MQTT 접속 실패: {exc}") from exc

    try:
        if args.seconds:
            import time

            client.loop_start()
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                time.sleep(0.5)
            client.loop_stop()
            print(f"\n{args.seconds}초 경과.")
        else:
            client.loop_forever()
    except KeyboardInterrupt:
        pass
    summary()
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    session = _load_session()
    print(f"userId     : {_mask(session['userId'])}")
    print(f"userSeq    : {session['userSeq']}")
    print(f"accountSeq : {session['accountSeq']}")
    for home in session["homes"]:
        print(f"home       : {home['homeSeq']} \"{home['nickname']}\" "
              f"기기 {len(home['devices'])}대")
    return 0



# ---------------------------------------------------------------- 에어원

AIRONE_MODE_NAMES = {0: "없음", 4: "환기", 5: "배기", 6: "요리", 8: "청정", 9: "제습",
                     10: "환기제습", 12: "자동운전", 15: "환기(외기)", 17: "바이패스",
                     18: "음압환기"}
AIRONE_OPTION_NAMES = {1: "", 2: "터보", 3: "절전", 4: "숙면", 5: "기저", 6: "기저"}
AIRONE_WIND_NAMES = {1: "미풍", 2: "약풍", 3: "강풍", 4: "자동", 5: "기저", 6: "기저"}
AIRONE_RUN_NAMES = {1: "운전", 2: "정지", 3: "외출"}


def _airone_mode_label(mode: Any, option: Any) -> str:
    base = AIRONE_MODE_NAMES.get(mode, f"알 수 없음({mode})")
    suffix = AIRONE_OPTION_NAMES.get(option) or ""
    return f"{base} {suffix}" if suffix else base


def _airone_prepare(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], str, int, int]:
    """세션과 기기를 잡고, 에어원인지·세대가 맞는지 검사한다."""
    session = _load_session()
    home_seq = args.home_seq or session["homes"][0]["homeSeq"]
    user_seq = session["userSeq"]
    token = session["accessToken"]
    dev = _find_device(token, home_seq, user_seq, args.device_seq)

    if dev.get("serviceCode") != 300:
        raise NavienError(
            f"deviceSeq {args.device_seq} 의 serviceCode 는 {dev.get('serviceCode')} 다. "
            "이 명령은 환기청정(300) 전용이다."
        )
    try:
        model_code = int(dev.get("modelCode"))
    except (TypeError, ValueError):
        raise NavienError(f"modelCode '{dev.get('modelCode')}' 를 숫자로 읽지 못했다.") from None
    if model_code < 1000:
        raise NavienError(
            f"modelCode {model_code} 는 구세대 통신을 쓴다 (봉투와 토픽이 다르다).\n"
            "  이 명령은 신형(1000 이상)만 다룬다. 추측으로 보내지 않는다."
        )
    return session, dev, token, home_seq, user_seq


def _airone_body(dev: dict[str, Any], command: str, client_id: str,
                 desired: dict[str, Any] | None) -> str:
    """`AironePubComm` 봉투를 만든다. 매트와 모양이 다르다."""
    rc = _dig(dev, "Properties", "data", "did", "reported", "roomController") or {}
    physical = rc.get("deviceId") or dev["deviceId"]
    topic = f"cmd/rc/v2/{dev['modelCode']}/{physical}/remote/{command}"
    payload: dict[str, Any] = {
        "clientId": client_id,
        "sessionId": str(int(time.time() * 1000)),
        "requestTopic": topic,
        "responseTopic": f"{topic}/res",
    }
    if desired is not None:
        payload["state"] = {"desired": desired}
    body = json.dumps({"serviceCode": dev["serviceCode"], "payload": payload},
                      ensure_ascii=False)
    for value in (payload["responseTopic"], topic):
        quoted = json.dumps(value)
        body = body.replace(quoted, quoted.replace("/", "\\/"))
    return body


def cmd_airone_modes(args: argparse.Namespace) -> int:
    """서버가 알려준 운전 조합을 표로 본다. **아무것도 보내지 않는다.**

    이 표가 통합이 선택 항목을 만드는 근거다. 제보할 때 이것부터 붙이면 된다.
    """
    _, dev, _, _, _ = _airone_prepare(args)
    did = _dig(dev, "Properties", "data", "did", "reported") or {}
    rc = did.get("roomController") or {}
    odu = did.get("odu") or {}

    print(f"대상     : deviceSeq {args.device_seq}  \"{_dig(dev, 'Properties', 'nickName', 'mainItem')}\"")
    print(f"모델     : {dev.get('modelName')}  modelCode={dev.get('modelCode')}")
    print(f"실내기   : deviceId={_mask(rc.get('deviceId'))} version={rc.get('version')} "
          f"zoneId={rc.get('zoneId')}")
    print(f"실외기   : modelCode={odu.get('modelCode')} version={odu.get('version')} "
          f"airCapacity={odu.get('airCapacity')}")
    print(f"연결     : {dev.get('connected')}")
    print()

    modes = rc.get("mode") or []
    if not modes:
        print("서버가 mode 목록을 주지 않았다. 통합도 모드 선택을 만들 수 없다.")
        return 0

    print(f"운전 조합 {len(modes)}개:")
    print(f"  {'mode':>5} {'option':>7} {'지금풍량':>10} {'고를수있는풍량':>18}  라벨")
    for item in modes:
        wind = item.get("airVolume")
        wind_txt = f"{wind}"
        if wind in AIRONE_WIND_NAMES:
            wind_txt = f"{wind}({AIRONE_WIND_NAMES[wind]})"
        elif wind is not None:
            wind_txt = f"{wind}(?)"
        # `supportedAirVolumes` 가 고를 수 있는 목록이다. `airVolume` 은 지금 값이다.
        supported = item.get("supportedAirVolumes") or []
        sup_txt = ",".join(
            f"{v}({AIRONE_WIND_NAMES.get(v, '?')})" for v in supported
        ) or "-"
        print(f"  {_fmt(item.get('name')):>5} {_fmt(item.get('option')):>7} {wind_txt:>10} "
              f"{sup_txt:>18}  "
              f"{_airone_mode_label(item.get('name'), item.get('option'))}")
        for extra in item.get("additionalData") or []:
            print(f"        additionalData type={extra.get('type')} "
                  f"min={extra.get('min')} max={extra.get('max')} value={extra.get('value')}")

    unknown = sorted({w for i in modes
                      if (w := i.get("airVolume")) not in (None, 0)
                      and w not in AIRONE_WIND_NAMES})
    if unknown:
        print()
        print(f"※ 확인되지 않은 airVolume 값: {unknown}")
        print("  통합은 이 값을 보내지 않는다. 이 출력을 이슈에 붙여 주면 판정할 수 있다.")

    sensors = rc.get("sensor") or []
    if sensors:
        print()
        print("센서 종류(정수 코드 — 뜻은 미확인):")
        for s in sensors:
            print(f"  type={s.get('type')} min={s.get('min')} max={s.get('max')}")
    return 0


def cmd_airone_status(args: argparse.Namespace) -> int:
    """상태를 올려달라고 요청한다.

    응답은 MQTT 로 온다 — `watch --prefix airone` 를 따로 띄워 두고 이걸 실행한다.
    공기질은 REST 로 바로 읽는다.
    """
    _, dev, token, home_seq, user_seq = _airone_prepare(args)
    client_id = f"{uuid.uuid4()}-U{user_seq}"
    body = _airone_body(dev, "status", client_id, None)

    print(f"대상   : deviceSeq {args.device_seq}  \"{_dig(dev, 'Properties', 'nickName', 'mainItem')}\"")
    print(f"연결   : {dev.get('connected')}")
    print()
    print("보낼 본문 (상태 조회 — state 가 없다):")
    print("  " + body)
    print()

    if not args.yes:
        print("실제로 보내려면 --yes 를 붙인다. 아무것도 보내지 않고 종료한다.")
        return 0

    res = _api("POST", f"/devices/{args.device_seq}/control", token,
               query={"homeSeq": home_seq, "userSeq": user_seq}, raw_body=body)
    print(f"전송 완료. code={res.get('code')} msg={res.get('msg')}")
    print("상태 응답은 MQTT 로 온다 — 다른 창에서 `watch --prefix airone` 로 확인한다.")
    print()

    try:
        air = _api("GET", f"/devices/{args.device_seq}/air-sensor", token,
                   query={"homeSeq": home_seq, "userSeq": user_seq})
    except NavienError as exc:
        print(f"공기질 조회 실패: {exc}")
        return 0

    print("공기질 (/air-sensor):")
    print("  " + json.dumps(air.get("data"), ensure_ascii=False))
    return 0


def cmd_airone_control(args: argparse.Namespace) -> int:
    """운전 모드·풍량·습도를 바꾼다. **기기가 실제로 반응한다.**

    서버가 알려준 조합에 없는 값은 거부한다. 추측으로 보내지 않는다.
    """
    _, dev, token, home_seq, user_seq = _airone_prepare(args)
    rc_did = _dig(dev, "Properties", "data", "did", "reported", "roomController") or {}
    modes = rc_did.get("mode") or []

    if args.power is not None:
        running = 1 if args.power == "on" else 2
        controller: dict[str, Any] = {"deviceId": rc_did.get("deviceId") or dev["deviceId"],
                                      "running": running}
        if rc_did.get("zoneId") is not None:
            controller["zoneId"] = rc_did["zoneId"]
        command, desired = "power", {"roomController": controller}
        summary = f"전원 {args.power} (running={running})"
    else:
        if args.mode is None:
            raise NavienError("--power 또는 --mode 중 하나는 지정한다.")
        option = args.option
        matching = [m for m in modes
                    if m.get("name") == args.mode and m.get("option") == option]
        if not matching:
            available = sorted({(m.get("name"), m.get("option")) for m in modes})
            raise NavienError(
                f"서버가 알려준 조합에 (mode={args.mode}, option={option}) 이 없다.\n"
                f"  있는 조합: {available}\n"
                "  없는 조합을 보내지 않는다. `airone-modes` 로 확인한다."
            )

        controller = {"mode": args.mode, "option": option}
        if args.wind is not None:
            allowed = set()
            for m in matching:
                for v in m.get("supportedAirVolumes") or []:
                    if v in AIRONE_WIND_NAMES:
                        allowed.add(v)
                if not m.get("supportedAirVolumes") and m.get("airVolume") in AIRONE_WIND_NAMES:
                    allowed.add(m["airVolume"])
            allowed = sorted(allowed)
            if args.wind not in allowed:
                raise NavienError(
                    f"풍량 {args.wind} 는 이 조합에서 서버가 알려준 값이 아니다.\n"
                    f"  가능한 값: {allowed or '없음'}"
                )
            controller["airVolume"] = args.wind
        if args.humidity is not None:
            bounds = None
            for m in matching:
                for extra in m.get("additionalData") or []:
                    if extra.get("type") == 1 and extra.get("min") is not None:
                        bounds = (extra["min"], extra["max"])
                        break
            if bounds is None:
                raise NavienError(
                    "이 조합에는 서버가 습도 범위를 알려주지 않았다. 습도를 보내지 않는다."
                )
            if not (bounds[0] <= args.humidity <= bounds[1]):
                raise NavienError(f"습도 {args.humidity} 는 범위 {bounds[0]}~{bounds[1]} 를 벗어난다.")
            controller["additionalData"] = {"type": 1, "value": args.humidity}

        command, desired = "change-mode", {"roomController": controller}
        summary = _airone_mode_label(args.mode, option)
        if args.wind is not None:
            summary += f", 풍량 {args.wind}({AIRONE_WIND_NAMES.get(args.wind)})"
        if args.humidity is not None:
            summary += f", 목표습도 {args.humidity}%"

    client_id = f"{uuid.uuid4()}-U{user_seq}"
    body = _airone_body(dev, command, client_id, desired)

    print(f"대상   : deviceSeq {args.device_seq}  \"{_dig(dev, 'Properties', 'nickName', 'mainItem')}\"")
    print(f"모델   : {dev.get('modelName')}  modelCode={dev.get('modelCode')}")
    print(f"연결   : {dev.get('connected')}")
    print(f"설정   : {summary}")
    print()
    print("보낼 본문:")
    print("  " + body)
    print()
    print("※ 이 규약은 앱에서 뽑았지만 실기기로 검증되지 않았다.")
    print("  결과가 어떻든(되든 안 되든) 이 출력과 함께 알려 주면 고칠 수 있다.")
    print()

    if not args.yes:
        print("실제로 보내려면 --yes 를 붙인다. 아무것도 보내지 않고 종료한다.")
        return 0

    if not dev.get("connected"):
        print("경고: 기기가 오프라인이다(connected=0).")

    res = _api("POST", f"/devices/{args.device_seq}/control", token,
               query={"homeSeq": home_seq, "userSeq": user_seq}, raw_body=body)
    print(f"전송 완료. code={res.get('code')} msg={res.get('msg')}")
    print("반영은 `watch --prefix airone` 로 확인한다. 이 명령은 재전송하지 않는다.")
    return 0


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(
        description="나비엔 스마트 조회용 CLI (읽기 전용 — 제어 명령을 보내지 않는다)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="로그인하고 세션을 저장한다")
    p_login.add_argument("-u", "--username", help="나비엔 아이디 (없으면 .env 나 입력으로 받는다)")
    p_login.set_defaults(func=cmd_login)

    p_dev = sub.add_parser("devices", help="기기 목록과 기능 메타데이터를 본다")
    p_dev.add_argument("--home-seq", type=int, help="home 이 여러 개일 때 지정")
    p_dev.add_argument("--raw", action="store_true", help="응답 JSON 을 그대로 출력")
    p_dev.add_argument("--no-redact", action="store_true", help="기기ID·IP·MAC 을 가리지 않는다")
    p_dev.set_defaults(func=cmd_devices)

    p_watch = sub.add_parser(
        "watch",
        help="기기가 올리는 실시간 보고를 구독한다 (구독만 — 발행 안 함)",
    )
    p_watch.add_argument("--home-seq", type=int, help="home 이 여러 개일 때 지정")
    p_watch.add_argument("--prefix", default="mate",
                         help="토픽 접두사. 기본 mate (보일러·에어원은 다를 수 있다)")
    p_watch.add_argument("--seconds", type=int, default=0,
                         help="이 초만큼만 듣고 종료한다. 0 이면 Ctrl+C 까지 계속")
    p_watch.add_argument("--region", default=IOT_REGION, help="AWS 리전")
    p_watch.add_argument("--endpoint",
                         help="IoT 엔드포인트를 직접 지정 (기본은 DescribeEndpoint 로 조회)")
    p_watch.set_defaults(func=cmd_watch)

    p_ctl = sub.add_parser(
        "control",
        help="단계 제어 명령을 보낸다 (--yes 없이는 본문만 출력하고 끝난다)",
    )
    p_ctl.add_argument("--device-seq", type=int, required=True)
    p_ctl.add_argument("--home-seq", type=int)
    p_ctl.add_argument("--single", type=int, help="싱글 매트 단계")
    p_ctl.add_argument("--left", type=int, help="좌측 단계")
    p_ctl.add_argument("--right", type=int, help="우측 단계")
    p_ctl.add_argument("--yes", action="store_true", help="실제로 전송한다")
    p_ctl.set_defaults(func=cmd_control)

    p_am = sub.add_parser(
        "airone-modes",
        help="환기청정이 지원하는 운전 조합을 본다 (아무것도 보내지 않는다)",
    )
    p_am.add_argument("--device-seq", type=int, required=True)
    p_am.add_argument("--home-seq", type=int)
    p_am.set_defaults(func=cmd_airone_modes)

    p_as = sub.add_parser(
        "airone-status",
        help="환기청정에 상태를 올려달라고 요청하고 공기질을 읽는다",
    )
    p_as.add_argument("--device-seq", type=int, required=True)
    p_as.add_argument("--home-seq", type=int)
    p_as.add_argument("--yes", action="store_true", help="실제로 전송한다")
    p_as.set_defaults(func=cmd_airone_status)

    p_ac = sub.add_parser(
        "airone-control",
        help="환기청정 운전 모드·풍량·습도를 바꾼다 (--yes 없이는 본문만 출력)",
    )
    p_ac.add_argument("--device-seq", type=int, required=True)
    p_ac.add_argument("--home-seq", type=int)
    p_ac.add_argument("--power", choices=("on", "off"), help="전원만 바꾼다")
    p_ac.add_argument("--mode", type=int,
                      help="운전 모드 (4 환기 / 6 요리 / 8 청정 / 9 제습 / 10 환기제습 / "
                           "12 자동 / 17 바이패스)")
    p_ac.add_argument("--option", type=int, default=1,
                      help="옵션 (1 없음 / 2 터보 / 3 절전 / 4 숙면 / 5,6 기저). 기본 1")
    p_ac.add_argument("--wind", type=int,
                      help="풍량 (1 미풍 / 2 약풍 / 3 강풍 / 4 자동 / 5,6 기저)")
    p_ac.add_argument("--humidity", type=int, help="목표 습도 %% (제습 계열에서만)")
    p_ac.add_argument("--yes", action="store_true", help="실제로 전송한다")
    p_ac.set_defaults(func=cmd_airone_control)

    p_who = sub.add_parser("whoami", help="저장된 세션 정보를 본다")
    p_who.set_defaults(func=cmd_whoami)

    args = parser.parse_args()
    try:
        return args.func(args)
    except NavienError as exc:
        print(f"\n오류: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
