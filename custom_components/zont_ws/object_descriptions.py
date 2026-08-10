"""Russian Home Assistant presentation for supported ZONT objects."""

from types import MappingProxyType

HEATING_CIRCUIT_SUBTYPE_NAMES = MappingProxyType(
    {
        0: "Котловой контур",
        1: "Контур ГВС",
        2: "Охладительный контур",
        3: "Контур потребителя",
    }
)

ANALOG_INPUT_SUBTYPE_NAMES = MappingProxyType(
    {
        0: "Аналоговый вход без пресета",
        1: "Датчик давления 5 бар",
        2: "Датчик давления 12 бар",
        3: "Датчик открытия двери",
        4: "ИК-датчик движения с контролем шлейфа",
        5: "Датчик дыма",
        6: "Датчик протечки",
        7: "ИК-датчик движения без контроля шлейфа",
        8: "Комнатный термостат",
        9: "Авария котла +",
        10: "Авария котла -",
        11: "Вход «Зажигание»",
        12: "Датчик скорости",
        13: "Датчик оборотов двигателя",
        14: "Дискретный вход",
        15: "Тревожная кнопка",
        16: "Датчик расхода топлива",
        17: "Датчик влажности",
        18: "Датчик давления 6 бар",
        19: "Дискретный вход НР",
        20: "Дискретный вход НЗ",
        21: "Датчик давления 10 бар",
    }
)

RADIO_SENSOR_SUBTYPE_NAMES = MappingProxyType(
    {
        2: "Трёхкнопочный брелок",
        3: "Четырёхкнопочный брелок",
        4: "Радиореле блокировки",
        5: "Радиотермометр",
        6: "Радиодут",
        7: "Модуль капота",
        8: "Радиометка",
        10: "Радиодатчик протечки",
        11: "Радиодатчик движения",
        12: "Радиодатчик удара",
        13: "Радиотермометр из трёх датчиков и концевого контакта",
        14: "Радиодатчик расхода электроэнергии",
        15: "Внешний радиодатчик температуры",
        16: "Радиодатчик расхода воды и газа",
        17: "Радиорозетка 220 В",
        18: "Радиодатчик температуры и влажности",
        19: "Радиометка с акселерометром",
        20: "Радиометка с аккумулятором",
        23: "Радиопанель или радиотермостат",
    }
)

SUPPORTED_RADIO_SENSOR_SUBTYPES = frozenset({5, 10, 11, 15, 18})


def analog_input_model(subtype: int) -> str:
    """Return the documented display name for an analog input subtype."""
    return ANALOG_INPUT_SUBTYPE_NAMES.get(
        subtype,
        f"Аналоговый вход (подтип {subtype})",
    )


def radio_sensor_model(subtype: int) -> str:
    """Return the documented display name for a radio sensor subtype."""
    return RADIO_SENSOR_SUBTYPE_NAMES.get(
        subtype,
        f"Радиодатчик (подтип {subtype})",
    )


def heating_circuit_model(subtype: int) -> str:
    """Return the documented display name for a heating circuit subtype."""
    return HEATING_CIRCUIT_SUBTYPE_NAMES.get(
        subtype,
        f"Контур отопления (подтип {subtype})",
    )


def object_device_identifier(controller_identifier: str, object_id: int) -> str:
    """Return a stable device registry identifier for one controller object."""
    return f"{controller_identifier}:object:{object_id}"
