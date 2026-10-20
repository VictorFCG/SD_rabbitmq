# Ajuste do console no Windows. No console classico (conhost) a "Edicao Rapida"
# (QuickEdit) vem ligada: clicar ou selecionar texto na janela de um servico pausa
# o processo inteiro ate apertar Esc/Enter (o servico para de responder no meio da
# apresentacao). Cada processo desliga a Edicao Rapida da propria janela ao iniciar.
# Fora do Windows, ou sem console (saida redirecionada), nao faz nada.
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
    except Exception:  # noqa: BLE001 - ajuste opcional, nunca impede o servico de subir
        pass
