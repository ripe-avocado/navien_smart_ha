"""나비엔 스마트 통합.

나비엔 스마트 앱이 쓰는 서버에 직접 붙는다. 공식 API 가 아니다.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import NavienSmartApi, NavienSmartAuthError, NavienSmartError
from .const import CONF_HOME_SEQ, DOMAIN
from .coordinator import NavienSmartCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    # 온도형(`0.5C`)은 climate, 단계형(`1.0L`)은 number 로 갈린다. 둘 다 등록하고
    # 각 플랫폼이 자기 축의 기기만 골라 간다.
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

NavienSmartConfigEntry = ConfigEntry[NavienSmartCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: NavienSmartConfigEntry) -> bool:
    """자격증명으로 로그인하고 코디네이터를 세운다."""
    # 폼 로그인이 쿠키를 물어야 해서 전용 세션을 쓴다. 공용 세션을 오염시키지 않는다.
    http = async_create_clientsession(hass)
    api = NavienSmartApi(http, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    try:
        session = await api.async_login()
    except NavienSmartAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except NavienSmartError as err:
        raise ConfigEntryNotReady(str(err)) from err

    home_seq = entry.data.get(CONF_HOME_SEQ) or session.homes[0]["homeSeq"]

    coordinator = NavienSmartCoordinator(hass, entry, api, int(home_seq))
    await coordinator.async_config_entry_first_refresh()

    if not coordinator.data:
        _LOGGER.warning(
            "home %s 에서 지원 가능한 기기를 찾지 못했습니다. "
            "건너뛴 기기가 있으면 위 경고를 확인해 주세요.",
            home_seq,
        )

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 엔티티가 준비된 뒤 구독을 시작한다.
    await coordinator.async_start_mqtt()
    entry.async_on_unload(coordinator.async_stop_mqtt)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NavienSmartConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: NavienSmartConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
