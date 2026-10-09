"""Contrat d'entrée / sortie de l'API (validé par Pydantic, publié dans OpenAPI).
 
TP1, partie 2 : complétez les schémas. Mode : SANS IA pour cette partie.
"""
 
from __future__ import annotations
from datetime import UTC
 
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
 
 
class PredictionRequest(BaseModel):
    """Une observation de station à l'instant t.
 
    TODO 1 [Must] : déclarer les 6 champs attendus par le modèle (voir velov.features.RAW_COLUMNS)
             avec leur type Python. Pour timestamp, utilisez AwareDatetime et non datetime :
             datetime accepte "2026-10-06T08:00:00" sans fuseau, un instant ambigu.
    TODO 2 [Must] : ajouter des bornes avec Field(...) : station_id >= 1, 0 < capacity <= 100,
             bikes_available >= 0, température entre -30 et 50 °C.
    TODO 3 [Must] : refuser un champ inconnu (indice : model_config / extra).
    TODO 4 [Must] : refuser bikes_available > capacity (indice : @model_validator(mode="after")).
    TODO 4 bis [Should] : normaliser timestamp en UTC
             (indice : @field_validator("timestamp") et value.astimezone(UTC)).
    """
    # TODO 3 : refuse tout champ non déclaré
    model_config = ConfigDict(extra="forbid")
    # TODO 1 + TODO 2 : les 6 champs typés, avec leurs bornes
    station_id: int = Field(...,ge =1, description="Identifiant de la station")
    timestamp: AwareDatetime = Field(..., description="Instant de l'observation (avec fuseau)")
    capacity: int = Field(..., gt=0, le=100, description="Nombre total de bornes")
    bikes_available: int = Field(..., ge=0, description="Vélos disponibles")
    temperature: float = Field(..., ge=-30, le=50, description="Température en °C")
    is_raining: bool = Field(..., description="Pluie au moment de l'observation")
 
    #TODO 4 [Must] : refuser bikes_available > capacity (indice : @model_validator(mode="after")).  cohérence métier — pas plus de vélos que de bornes
    @model_validator(mode="after")
    def check_bikes_le_capacity(self) -> "PredictionRequest":
        if self.bikes_available > self.capacity:
            raise ValueError(
                f"bikes_available ({self.bikes_available}) "
                f"ne peut pas dépasser capacity ({self.capacity})"
            )
        return self
   
    #TODO 4 bis [Should] : normaliser timestamp en UTC (indice : @field_validator("timestamp") et value.astimezone(UTC)).
    @field_validator("timestamp")
    @classmethod
    def _to_utc(cls, value: AwareDatetime) -> AwareDatetime:
        return value.astimezone(UTC)
class PredictionResponse(BaseModel):
    station_id: int
    target_timestamp: AwareDatetime = Field(..., description="Instant prédit (t + 1 h)")
    predicted_bikes: float = Field(..., ge=0)
    model_version: str
 
 
# STRETCH : BatchPredictionRequest (1 à 1000 PredictionRequest) et BatchPredictionResponse
