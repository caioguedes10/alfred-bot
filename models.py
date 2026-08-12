"""
Modelos Pydantic para validação dos payloads de cada módulo antes do INSERT
em `logs_metricas` (Supabase/PostgreSQL, coluna JSONB).

Cada modelo espelha 1:1 o schema-padrão aprovado. Nenhum registro chega
ao banco sem passar por aqui — isso mitiga o risco de payload incompleto
vindo do parsing de texto livre do Gemini 3.6.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# Metas fixas de nutrição (usadas no cálculo de epsilon_macro, não no payload)
META_KCAL = 2000
META_PROTEINA_G = 180
META_CARBOIDRATO_G = 200
META_GORDURA_G = 60


class Modulo(str, Enum):
    ESTUDOS = "estudos"
    WWT = "wwt"
    NUTRICAO = "nutricao"
    TREINO = "treino"


# --------------------------------------------------------------------------- #
# 1. Estudos
# --------------------------------------------------------------------------- #
class MiniProva(BaseModel):
    acertos: int = Field(ge=0)
    total: int = Field(gt=0)

    @model_validator(mode="after")
    def acertos_nao_excede_total(self) -> "MiniProva":
        if self.acertos > self.total:
            raise ValueError("acertos não pode exceder total")
        return self


class EstudosPayload(BaseModel):
    tempo_estudo_min: int = Field(gt=0)
    topicos: list[str] = Field(min_length=1)
    explicacao_feynman: str = Field(min_length=1)
    mini_prova: MiniProva


# --------------------------------------------------------------------------- #
# 2. WWT (Operações / Fiscais-Comex)
# --------------------------------------------------------------------------- #
class Demanda(BaseModel):
    abertura: datetime
    finalizacao: datetime
    retrabalho: bool = False

    @field_validator("abertura", "finalizacao")
    @classmethod
    def exige_timezone(cls, v: datetime) -> datetime:
        # Mitiga o risco #2 do desenho: nunca aceitar timestamp naive.
        if v.tzinfo is None:
            raise ValueError("timestamp deve ser timezone-aware (ex.: -03:00)")
        return v

    @model_validator(mode="after")
    def finalizacao_apos_abertura(self) -> "Demanda":
        if self.finalizacao < self.abertura:
            raise ValueError("finalizacao não pode ser anterior à abertura")
        return self


class WWTPayload(BaseModel):
    demandas: list[Demanda] = Field(min_length=1)


# --------------------------------------------------------------------------- #
# 3. Nutrição
# --------------------------------------------------------------------------- #
class Refeicao(BaseModel):
    nome: str
    seguiu_plano: bool
    kcal: float = Field(ge=0)
    proteina_g: float = Field(ge=0)
    carboidrato_g: float = Field(ge=0)
    gordura_g: float = Field(ge=0)


class NutricaoPayload(BaseModel):
    refeicoes_meta: int = Field(gt=0)
    refeicoes: list[Refeicao] = Field(default_factory=list)

    @property
    def taxa_omissao(self) -> int:
        return max(self.refeicoes_meta - len(self.refeicoes), 0)

    @property
    def mae_calorico(self) -> float:
        total_kcal = sum(r.kcal for r in self.refeicoes)
        return abs(total_kcal - META_KCAL)

    @property
    def epsilon_macro(self) -> float:
        total_proteina = sum(r.proteina_g for r in self.refeicoes)
        total_carbo = sum(r.carboidrato_g for r in self.refeicoes)
        total_gordura = sum(r.gordura_g for r in self.refeicoes)
        return (
            abs(total_proteina - META_PROTEINA_G)
            + abs(total_carbo - META_CARBOIDRATO_G)
            + abs(total_gordura - META_GORDURA_G)
        )


# --------------------------------------------------------------------------- #
# 4. Treino (Hevy)
# --------------------------------------------------------------------------- #
class Serie(BaseModel):
    reps: int = Field(gt=0)
    peso_kg: float = Field(ge=0)


class Exercicio(BaseModel):
    nome: str

    @field_validator("nome")
    @classmethod
    def normaliza_nome(cls, v: str) -> str:
        # Mitiga o risco #3: "Supino Reto" vs "supino reto" quebrando
        # a comparação histórica de ΔP.
        return v.strip().lower()

    series: list[Serie] = Field(min_length=1)

    @property
    def volume(self) -> float:
        return sum(s.reps * s.peso_kg for s in self.series)


class Cardio(BaseModel):
    kcal: float = Field(ge=0)
    tempo_min: float = Field(gt=0)

    @property
    def kcal_por_min(self) -> float:
        return self.kcal / self.tempo_min


class TreinoPayload(BaseModel):
    tempo_treino_min: int = Field(gt=0)
    exercicios: list[Exercicio] = Field(default_factory=list)
    cardio: Optional[Cardio] = None

    @property
    def volume_total(self) -> float:
        return sum(e.volume for e in self.exercicios)

    @property
    def densidade_treino(self) -> float:
        return self.volume_total / self.tempo_treino_min


# --------------------------------------------------------------------------- #
# Envelope de ingestão — o que a rota Flask efetivamente recebe
# --------------------------------------------------------------------------- #
_PAYLOAD_POR_MODULO: dict[Modulo, type[BaseModel]] = {
    Modulo.ESTUDOS: EstudosPayload,
    Modulo.WWT: WWTPayload,
    Modulo.NUTRICAO: NutricaoPayload,
    Modulo.TREINO: TreinoPayload,
}


class LogMetricaCreate(BaseModel):
    data_referencia: date
    modulo: Modulo
    raw_input: Optional[str] = None
    payload: dict

    @model_validator(mode="after")
    def valida_payload_por_modulo(self) -> "LogMetricaCreate":
        modelo = _PAYLOAD_POR_MODULO[self.modulo]
        # Levanta ValidationError se o payload não bater com o schema do módulo
        modelo.model_validate(self.payload)
        return self
