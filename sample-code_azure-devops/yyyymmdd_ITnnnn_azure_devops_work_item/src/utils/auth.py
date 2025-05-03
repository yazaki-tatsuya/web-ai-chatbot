"""
認証関連のユーティリティ
"""

import base64
import logging

logger = logging.getLogger(__name__)


def create_basic_auth_header(pat: str) -> dict[str, str]:
    """
    Basic認証ヘッダーを作成

    Args:
        pat (str): Personal Access Token

    Returns:
        Dict[str, str]: 認証ヘッダー
    """
    auth_str = f":{pat}"
    auth_bytes = auth_str.encode("ascii")
    auth_b64 = base64.b64encode(auth_bytes).decode("ascii")

    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/json",
    }

    logger.debug("認証ヘッダーを作成しました")
    return headers