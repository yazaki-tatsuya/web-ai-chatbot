"""
Work Item API クライアント
"""

import logging
from typing import Any

from ..utils.auth import create_basic_auth_header

logger = logging.getLogger(__name__)


class WorkItemClient:
    """Work Item APIクライアント"""

    def __init__(self, organization: str, project: str, pat: str) -> None:
        """
        Work Item APIクライアントを初期化

        Args:
            organization (str): 組織名
            project (str): プロジェクト名
            pat (str): Personal Access Token
        """
        self.organization = organization
        self.project = project
        self.headers = create_basic_auth_header(pat)
        logger.debug(f"Work Item APIクライアントを初期化しました: 組織={organization}, プロジェクト={project}")

    def get_work_item(self, id: int) -> dict[str, Any]:
        """
        Work Itemを取得

        Args:
            id (int): Work Item ID

        Returns:
            Dict[str, Any]: Work Item情報
        """
        import requests

        url = f"https://dev.azure.com/{self.organization}/{self.project}/_apis/wit/workitems/{id}?api-version=6.0"
        response = requests.get(url, headers=self.headers)

        if response.status_code != 200:
            logger.error(f"Work Item取得に失敗しました: {response.status_code}, {response.text}")
            response.raise_for_status()

        logger.info(f"Work Item取得成功: ID={id}")
        return response.json()