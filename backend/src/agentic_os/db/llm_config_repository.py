"""LLM Configuration Repository"""

from sqlalchemy.orm import Session
from sqlalchemy import update
from agentic_os.db.models import LLMConfigModel
from datetime import datetime


class LLMConfigRepository:
    """Repository for LLM configuration persistence"""

    def __init__(self, db: Session):
        self.db = db

    def get_config(self, config_key: str = "default") -> dict | None:
        """Get LLM configuration by key"""
        config = self.db.query(LLMConfigModel).filter(
            LLMConfigModel.config_key == config_key
        ).first()

        if config:
            return {
                "provider": config.provider,
                "api_key": config.api_key,
                "model": config.model,
                "insights_enabled": config.insights_enabled if config.insights_enabled is not None else True,
            }
        return None

    def save_config(
        self,
        provider: str,
        api_key: str | None,
        model: str | None,
        config_key: str = "default",
        insights_enabled: bool = True,
    ) -> dict:
        """Save or update LLM configuration"""
        existing = self.db.query(LLMConfigModel).filter(
            LLMConfigModel.config_key == config_key
        ).first()

        if existing:
            self.db.execute(
                update(LLMConfigModel).where(
                    LLMConfigModel.config_key == config_key
                ).values(
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    insights_enabled=insights_enabled,
                    updated_at=datetime.utcnow()
                )
            )
        else:
            config = LLMConfigModel(
                config_key=config_key,
                provider=provider,
                api_key=api_key,
                model=model,
                insights_enabled=insights_enabled,
            )
            self.db.add(config)

        self.db.commit()

        return {
            "provider": provider,
            "api_key": api_key,
            "model": model,
            "insights_enabled": insights_enabled,
        }

    def update_insights_enabled(self, enabled: bool, config_key: str = "default") -> bool:
        """Toggle AI insights without touching credentials. Returns False if no config exists."""
        result = self.db.execute(
            update(LLMConfigModel).where(
                LLMConfigModel.config_key == config_key
            ).values(
                insights_enabled=enabled,
                updated_at=datetime.utcnow()
            )
        )
        self.db.commit()
        return result.rowcount > 0

    def delete_config(self, config_key: str = "default") -> bool:
        """Delete LLM configuration"""
        result = self.db.query(LLMConfigModel).filter(
            LLMConfigModel.config_key == config_key
        ).delete()
        self.db.commit()
        return result > 0
