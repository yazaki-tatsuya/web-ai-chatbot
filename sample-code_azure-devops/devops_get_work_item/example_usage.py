"""
Azure DevOps Work Item モジュールの使用例
"""
import os
import sys
# 親ディレクトリのパスを取得してsys.pathに追加
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from env import get_env_variable
from devops_common.api.work_item import WorkItemClient
from devops_common.models.work_item import WorkItem

def main():
    # ステップ 1: Azure DevOpsのプロジェクト名とPATを指定
    organization_name = get_env_variable("ORGANIZATION_NAME")
    project_name = get_env_variable("PROJECT_NAME")
    personal_access_token = get_env_variable("PERSONAL_ACCESS_TOKEN")

    # ステップ 2: WorkItemClientを初期化
    client = WorkItemClient(organization=organization_name, project=project_name, pat=personal_access_token)

    # ステップ 3: 作業項目を取得
    work_item_id = 16  # 取得したい作業項目のIDを指定
    work_item_data = client.get_work_item(work_item_id)

    # ステップ 4: データをモデルに変換
    work_item = WorkItem.from_api_response(work_item_data)

    # ステップ 5: JSON形式で出力
    print("JSON形式での出力:")
    print(work_item.model_dump_json(indent=2))

if __name__ == "__main__":
    main()