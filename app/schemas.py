"""请求/响应模型与输入校验。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .engine import MAX_K, MAX_LIGHTS, MAX_PATTERN_LEN, MAX_W, MIN_K, MIN_LIGHTS, MIN_PATTERN_LEN, MIN_W


class LightItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=20, description="灯名")
    pattern: str = Field(..., description="循环 0/1 串，2~80 位且同时含 0、1")

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("灯名不能为空")
        if len(v) > 20:
            raise ValueError("灯名最长 20 个字符")
        return v

    @field_validator("pattern")
    @classmethod
    def _pattern(cls, v: str) -> str:
        v = v.strip()
        if not (MIN_PATTERN_LEN <= len(v) <= MAX_PATTERN_LEN):
            raise ValueError(f"灯串长度须为 {MIN_PATTERN_LEN}~{MAX_PATTERN_LEN} 位")
        if any(c not in "01" for c in v):
            raise ValueError("灯串只能由字符 0、1 组成")
        if "0" not in v or "1" not in v:
            raise ValueError("灯串必须同时包含 0 和 1")
        return v


class LightsPayload(BaseModel):
    lights: list[LightItem] = Field(..., description="整批灯串（2~20 盏）")

    @field_validator("lights")
    @classmethod
    def _count(cls, v: list[LightItem]) -> list[LightItem]:
        if not (MIN_LIGHTS <= len(v) <= MAX_LIGHTS):
            raise ValueError(f"灯的数量须为 {MIN_LIGHTS}~{MAX_LIGHTS} 盏")
        names = [item.name for item in v]
        if len(set(names)) != len(names):
            raise ValueError("灯名不能重复")
        return v


class SettingsPayload(BaseModel):
    w: int = Field(..., ge=MIN_W, le=MAX_W, description=f"观测窗口上限 W（{MIN_W}~{MAX_W}）")
    k: int = Field(..., ge=MIN_K, le=MAX_K, description=f"每段最多误判位数 k（{MIN_K}~{MAX_K}）")


class AnalysisCreate(BaseModel):
    note: str = Field("", max_length=200, description="备注（可选）")


class InversionCreate(BaseModel):
    observation: str = Field(..., description="实际观测 0/1 串，1~W 位")
    e: int = Field(..., ge=0, le=3, description="误判位数上限 e（0~3）")
    note: str = Field("", max_length=200, description="备注（可选）")

    @field_validator("observation")
    @classmethod
    def _observation(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= 400):
            raise ValueError("观测串长度须为 1~400 位（不超过该快照的 W）")
        if any(c not in "01" for c in v):
            raise ValueError("观测串只能由字符 0、1 组成")
        return v
