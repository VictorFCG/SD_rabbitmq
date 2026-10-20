# Desliga a Edicao Rapida (QuickEdit) do console do Windows: com ela ligada,
# clicar na janela de um servico pausa o processo ate apertar Esc/Enter.
import os

ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080  # necessario para o Windows aceitar a mudanca acima
STD_INPUT_HANDLE = -10


def desativar_edicao_rapida():
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        entrada = ctypes.c_void_p(kernel32.GetStdHandle(STD_INPUT_HANDLE))
        modo = ctypes.c_uint32()
        if kernel32.GetConsoleMode(entrada, ctypes.byref(modo)):
            novo = (modo.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
            kernel32.SetConsoleMode(entrada, novo)
    except Exception:  # noqa: BLE001 - ajuste opcional
        pass
