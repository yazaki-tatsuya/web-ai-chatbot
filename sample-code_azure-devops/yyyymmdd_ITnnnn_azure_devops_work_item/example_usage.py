"""
Azure DevOps Work Item モジュールの使用例
"""
import os
import sys
# 親ディレクトリのパスを取得してsys.pathに追加
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from env import get_env_variable
from src.api.work_item import WorkItemClient
from src.models.work_item import WorkItem

def main():
    # ステップ 1: Azure DevOpsのプロジェクト名とPATを指定
    organization_name = get_env_variable("ORGANIZATION_NAME")
    project_name = get_env_variable("PROJECT_NAME")
    personal_access_token = get_env_variable("PERSONAL_ACCESS_TOKEN")
    print("取得した環境変数:", organization_name, project_name, personal_access_token)

    # ステップ 2: WorkItemClientを初期化
    client = WorkItemClient(organization=organization_name, project=project_name, pat=personal_access_token)

    # ステップ 3: 作業項目を取得
    work_item_id = 16  # 取得したい作業項目のIDを指定
    work_item_data = client.get_work_item(work_item_id)

    # デバッグ用: 取得したデータを出力
    print("取得した作業項目データ:", work_item_data)

    # ステップ 4: データをモデルに変換
    work_item = WorkItem.from_api_response(work_item_data)

    # # ステップ 5: 結果を表示
    # print("作業項目の詳細:")
    # print(work_item)

    # # ステップ 6: JSON形式で出力
    # print("JSON形式での出力:")
    # print(work_item.to_dict())

if __name__ == "__main__":
    main()