"""
Work Itemのデータモデル
"""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class WorkItem(BaseModel):
    """Work Itemモデル"""

    id: int = Field(..., description="Work Item ID")
    title: str = Field(..., description="タイトル")
    state: str = Field(..., description="状態")
    created_date: datetime = Field(..., description="作成日時")
    created_by: str = Field(..., description="作成者")
    description: Optional[str] = Field(None, description="説明")

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "WorkItem":
        """
        API応答からWorkItemモデルを作成

        Args:
            data (Dict[str, Any]): API応答のデータ

        Returns:
            WorkItem: WorkItemモデル
        """
        fields = data.get("fields", {})
        return cls(
            id=data.get("id"),
            title=fields.get("System.Title"),
            state=fields.get("System.State"),
            created_date=fields.get("System.CreatedDate"),
            created_by=fields.get("System.CreatedBy", {}).get("displayName"),
            description=fields.get("System.Description"),
        )