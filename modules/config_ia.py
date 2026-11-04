"""Preferências não secretas, com aliases para a UI e configurações antigas."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json

from modules.preset import DADOS_DIR

CONFIG_IA_PATH = DADOS_DIR / "config_ia.json"
MODELO_GEMINI_PADRAO = "gemini-3.5-flash-lite"
MODELO_MISTRAL_PADRAO = "mistral-small-latest"
MODELO_GROQ_PADRAO = "openai/gpt-oss-120b"
PROVIDERS = ("gemini", "mistral", "groq")


@dataclass(init=False)
class ConfigIA:
    max_chamadas_episodio: int = 4
    max_cortes: int = 5
    janela_segundos: float = 360.0
    sobreposicao_segundos: float = 30.0
    provider_ativo: str = "gemini"
    modelo_gemini: str = MODELO_GEMINI_PADRAO
    modelo_mistral: str = MODELO_MISTRAL_PADRAO
    modelo_groq: str = MODELO_GROQ_PADRAO
    fallback_automatico: bool = False
    fallback_autenticacao: bool = False
    provider_preferido: str | None = None
    ordem_fallback: list[str] = field(default_factory=lambda: ["mistral", "groq", "gemini"])

    def __init__(self, modelo: str | None = None, max_chamadas_episodio: int = 4,
                 max_cortes: int = 5, janela_segundos: float = 360.0,
                 sobreposicao_segundos: float = 30.0, provider: str = "gemini", *,
                 provider_ativo: str | None = None, modelo_gemini: str | None = None,
                 modelo_mistral: str | None = None, modelo_groq: str | None = None,
                 fallback_automatico: bool = False, fallback_autenticacao: bool = False,
                 provider_preferido: str | None = None, ordem_fallback: list[str] | None = None):
        # A assinatura posicional da Etapa 1 continua válida.
        self.max_chamadas_episodio = max_chamadas_episodio
        self.max_cortes = max_cortes
        self.janela_segundos = janela_segundos
        self.sobreposicao_segundos = sobreposicao_segundos
        self.provider_ativo = provider if provider_ativo is None else provider_ativo
        self.modelo_gemini = modelo_gemini if modelo_gemini is not None else MODELO_GEMINI_PADRAO
        self.modelo_mistral = modelo_mistral if modelo_mistral is not None else MODELO_MISTRAL_PADRAO
        self.modelo_groq = modelo_groq if modelo_groq is not None else MODELO_GROQ_PADRAO
        self.fallback_automatico = fallback_automatico
        self.fallback_autenticacao = fallback_autenticacao
        self.provider_preferido = provider_preferido
        self.ordem_fallback = list(ordem_fallback) if ordem_fallback is not None else ["mistral", "groq", "gemini"]
        especificos = {"gemini": modelo_gemini, "mistral": modelo_mistral, "groq": modelo_groq}
        if modelo is not None and self.provider_ativo in PROVIDERS and especificos[self.provider_ativo] is None:
            self.modelo = modelo

    @property
    def provider(self) -> str:
        return self.provider_ativo

    @provider.setter
    def provider(self, valor: str):
        self.provider_ativo = valor

    @property
    def modelo(self) -> str:
        return getattr(self, f"modelo_{self.provider_ativo}")

    @modelo.setter
    def modelo(self, valor: str):
        if self.provider_ativo not in PROVIDERS:
            raise ValueError("Provider não suportado.")
        setattr(self, f"modelo_{self.provider_ativo}", valor)

    def validar(self) -> "ConfigIA":
        if type(self.fallback_automatico) is not bool or type(self.fallback_autenticacao) is not bool:
            raise ValueError("As opções de fallback devem ser booleanas.")
        if self.provider_preferido is not None and self.provider_preferido not in PROVIDERS:
            raise ValueError("Provedor preferido inválido.")
        if len(self.ordem_fallback) != 3 or set(self.ordem_fallback) != set(PROVIDERS):
            raise ValueError("A ordem de fallback deve conter os três providers, sem repetição.")
        if self.provider_ativo not in PROVIDERS:
            raise ValueError(f"Provider não suportado: {self.provider_ativo}")
        self.modelo = self.modelo.strip().removeprefix("models/")
        if not self.modelo:
            raise ValueError("Selecione um modelo de IA.")
        if not 1 <= int(self.max_chamadas_episodio) <= 20:
            raise ValueError("O limite deve ficar entre 1 e 20 chamadas por episódio.")
        if not 1 <= int(self.max_cortes) <= 10:
            raise ValueError("A quantidade de cortes deve ficar entre 1 e 10.")
        if self.janela_segundos < 120 or self.sobreposicao_segundos < 0:
            raise ValueError("Configuração de janelas inválida.")
        return self


def carregar_config_ia() -> ConfigIA:
    if not CONFIG_IA_PATH.exists():
        return ConfigIA()
    try:
        dados = json.loads(CONFIG_IA_PATH.read_text(encoding="utf-8"))
        permitidos = {*ConfigIA.__dataclass_fields__, "modelo", "provider"}
        return ConfigIA(**{k: v for k, v in dados.items() if k in permitidos}).validar()
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return ConfigIA()


def salvar_config_ia(config: ConfigIA) -> None:
    config.validar()
    CONFIG_IA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporario = CONFIG_IA_PATH.with_suffix(".json.tmp")
    temporario.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    temporario.replace(CONFIG_IA_PATH)
