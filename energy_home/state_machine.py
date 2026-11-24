"""State machines (Stage 5: State Diagrams 5.1 and 5.2 from 1laba_full_fc613401.pdf)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class DeviceState(str, Enum):
    """Smart appliance lifecycle states and substates (Diagram 5.1, page 16)."""

    # Top-level (composite) states
    OFF = "Выключен"
    ON = "Включен"
    EMERGENCY_MODE = "Аварийный_режим"
    RETIRED = "Списан"

    # Substates of OFF
    POWER_OFF_MODE = "РежимОбесточивания"

    # Substates of ON
    IDLE_WAITING = "Ожидание_активности"
    NORMAL_OPERATION = "Работа_в_штатном_режиме"
    FLEXIBLE_LOAD = "Гибкая_нагрузка"

    # Substates of EMERGENCY_MODE
    ANOMALY_DETECTED = "Обнаружение_аномалии"
    LINE_DISCONNECTED = "Отключение_линии"


@dataclass
class DeviceStateMachine:
    """State pattern implementation for a smart appliance per Diagram 5.1 (page 16)."""

    outer_state: DeviceState = DeviceState.OFF
    inner_state: Optional[DeviceState] = DeviceState.POWER_OFF_MODE
    transition_log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.transition_log:
            self.transition_log.append(
                "power off -> OFF / POWER_OFF_MODE (entry: full power reset)"
            )

    @property
    def current_state(self) -> DeviceState:
        return self.inner_state or self.outer_state

    def turn_on(self) -> DeviceState:
        """OFF -> ON (enters IDLE_WAITING)."""
        if self.outer_state != DeviceState.OFF:
            raise ValueError(
                f"Нельзя включить прибор из состояния «{self.outer_state.value}»."
            )
        self.outer_state = DeviceState.ON
        self.inner_state = DeviceState.IDLE_WAITING
        self.transition_log.append("turn_on -> ON / IDLE_WAITING")
        return self.current_state

    def turn_off(self) -> DeviceState:
        """ON -> OFF (entry: full power reset)."""
        if self.outer_state != DeviceState.ON:
            raise ValueError(
                f"Нельзя выключить прибор из состояния «{self.outer_state.value}»."
            )
        self.outer_state = DeviceState.OFF
        self.inner_state = DeviceState.POWER_OFF_MODE
        self.transition_log.append("turn_off -> OFF / POWER_OFF_MODE")
        return self.current_state

    def start_operation(self) -> DeviceState:
        """IDLE_WAITING -> NORMAL_OPERATION (power-on signal / start)."""
        if (
            self.outer_state != DeviceState.ON
            or self.inner_state != DeviceState.IDLE_WAITING
        ):
            raise ValueError(
                f"Старт работы доступен только из «Ожидание_активности», сейчас «{self.current_state.value}»."
            )
        self.inner_state = DeviceState.NORMAL_OPERATION
        self.transition_log.append("start_operation -> NORMAL_OPERATION")
        return self.current_state

    def switch_to_off_peak(self) -> DeviceState:
        """NORMAL_OPERATION -> FLEXIBLE_LOAD ([off-peak tariff window active])."""
        if self.outer_state != DeviceState.ON or self.inner_state not in (
            DeviceState.NORMAL_OPERATION,
            DeviceState.IDLE_WAITING,
        ):
            raise ValueError(
                f"Перенос в гибкую нагрузку недоступен из состояния «{self.current_state.value}»."
            )
        self.inner_state = DeviceState.FLEXIBLE_LOAD
        self.transition_log.append("switch_to_off_peak -> FLEXIBLE_LOAD")
        return self.current_state

    def finish_flexible_cycle(self) -> DeviceState:
        """FLEXIBLE_LOAD -> NORMAL_OPERATION ([work cycle completed])."""
        if self.inner_state != DeviceState.FLEXIBLE_LOAD:
            raise ValueError(
                f"Завершение гибкого цикла доступно только из «Гибкая_нагрузка», сейчас «{self.current_state.value}»."
            )
        self.inner_state = DeviceState.NORMAL_OPERATION
        self.transition_log.append("finish_flexible_cycle -> NORMAL_OPERATION")
        return self.current_state

    def finish_normal_operation(self) -> DeviceState:
        """NORMAL_OPERATION -> IDLE_WAITING (normal work completion)."""
        if self.inner_state != DeviceState.NORMAL_OPERATION:
            raise ValueError(
                f"Штатное завершение доступно только из «Работа_в_штатном_режиме», сейчас «{self.current_state.value}»."
            )
        self.inner_state = DeviceState.IDLE_WAITING
        self.transition_log.append("finish_normal_operation -> IDLE_WAITING")
        return self.current_state

    def trigger_anomaly(
        self,
        critical_threshold_exceeded: bool = True,
        notify_callback: Optional[Callable[[], None]] = None,
    ) -> DeviceState:
        """ON -> EMERGENCY_MODE -> ANOMALY_DETECTED [-> LINE_DISCONNECTED]."""
        if self.outer_state != DeviceState.ON:
            raise ValueError(
                f"Аварийный режим может наступить только для включённого прибора, сейчас «{self.outer_state.value}»."
            )
        self.outer_state = DeviceState.EMERGENCY_MODE
        self.inner_state = DeviceState.ANOMALY_DETECTED
        self.transition_log.append("trigger_anomaly -> EMERGENCY_MODE / ANOMALY_DETECTED")
        if critical_threshold_exceeded:
            if notify_callback is not None:
                notify_callback()
            self.inner_state = DeviceState.LINE_DISCONNECTED
            self.transition_log.append(
                "critical_threshold_exceeded -> LINE_DISCONNECTED (emergency shutdown)"
            )
        return self.current_state

    def clear_fault(self) -> DeviceState:
        """EMERGENCY_MODE -> OFF (fault cleared / error reset)."""
        if self.outer_state != DeviceState.EMERGENCY_MODE:
            raise ValueError(
                f"Сброс ошибки доступен только в аварийном режиме, сейчас «{self.outer_state.value}»."
            )
        self.outer_state = DeviceState.OFF
        self.inner_state = DeviceState.POWER_OFF_MODE
        self.transition_log.append("clear_fault -> OFF / POWER_OFF_MODE")
        return self.current_state

    def retire(self) -> DeviceState:
        """ON or OFF -> RETIRED (device removed / decommissioned)."""
        if self.outer_state == DeviceState.RETIRED:
            raise ValueError("Устройство уже списано.")
        self.outer_state = DeviceState.RETIRED
        self.inner_state = None
        self.transition_log.append("retire -> RETIRED")
        return self.current_state

    def available_actions(self) -> list[dict[str, str]]:
        """List valid transitions from the current state for the UI."""
        if self.outer_state == DeviceState.OFF:
            return [
                {"id": "enable", "label": "Включить прибор / подать питание"},
                {"id": "retire", "label": "Списать устройство"},
            ]
        if self.outer_state == DeviceState.EMERGENCY_MODE:
            return [
                {"id": "reset", "label": "Устранение аварии / сброс ошибки"},
            ]
        if self.outer_state == DeviceState.RETIRED:
            return []
        actions: list[dict[str, str]] = []
        if self.inner_state == DeviceState.IDLE_WAITING:
            actions.append({"id": "start", "label": "Сигнал включения / старт работы"})
            actions.append({"id": "flexible", "label": "Перенести на льготный тариф"})
        elif self.inner_state == DeviceState.NORMAL_OPERATION:
            actions.append({"id": "flexible", "label": "Перенести на льготный тариф"})
            actions.append({"id": "finish", "label": "Штатное завершение работы"})
        elif self.inner_state == DeviceState.FLEXIBLE_LOAD:
            actions.append({"id": "finish_flexible", "label": "Завершить цикл гибкой нагрузки"})
        actions.append({"id": "anomaly", "label": "Событие аномалии (тест защиты)"})
        actions.append({"id": "disable", "label": "Выключить прибор"})
        actions.append({"id": "retire", "label": "Списать устройство"})
        return actions


class WindowState(str, Enum):
    """Five application windows per State Diagram 5.2 (page 17)."""

    AUTH_WINDOW = "auth"
    MAIN_WINDOW = "main"
    DEVICES_WINDOW = "devices"
    REPORTS_WINDOW = "reports"
    SETTINGS_WINDOW = "settings"
    CLOSED = "closed"


@dataclass
class WindowTransitionFSM:
    """Finite state machine for transitions between the 5 windows (Diagram 5.2, page 17)."""

    current_window: WindowState = WindowState.AUTH_WINDOW

    ALLOWED_TRANSITIONS: dict[WindowState, tuple[WindowState, ...]] = field(
        default_factory=lambda: {
            WindowState.AUTH_WINDOW: (
                WindowState.AUTH_WINDOW,
                WindowState.MAIN_WINDOW,
            ),
            WindowState.MAIN_WINDOW: (
                WindowState.MAIN_WINDOW,
                WindowState.DEVICES_WINDOW,
                WindowState.REPORTS_WINDOW,
                WindowState.SETTINGS_WINDOW,
                WindowState.AUTH_WINDOW,
                WindowState.CLOSED,
            ),
            WindowState.DEVICES_WINDOW: (WindowState.MAIN_WINDOW,),
            WindowState.REPORTS_WINDOW: (WindowState.MAIN_WINDOW,),
            WindowState.SETTINGS_WINDOW: (WindowState.MAIN_WINDOW,),
            WindowState.CLOSED: (),
        },
        repr=False,
    )

    def transition(self, target_window: WindowState, is_owner: bool = False) -> WindowState:
        """Transition between screens per Diagram 5.2 enforcing [Role == Owner] for settings."""
        allowed = self.ALLOWED_TRANSITIONS.get(self.current_window, ())
        if target_window not in allowed:
            raise ValueError(
                f"Переход из «{self.current_window.value}» в «{target_window.value}» запрещён диаграммой 5.2."
            )
        if target_window == WindowState.SETTINGS_WINDOW and not is_owner:
            raise PermissionError("Доступ к «ОкноНастроек» разрешён только при [Роль == Владелец].")
        self.current_window = target_window
        return self.current_window
