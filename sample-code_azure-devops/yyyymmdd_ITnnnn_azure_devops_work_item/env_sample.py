def get_env_variable(key):

    env_variable_dict = {
        "ORGANIZATION_NAME" : "xxx",
        "PROJECT_NAME" : "xxx",
        # Azure DevOps Personal Access Token (PAT)
        "PERSONAL_ACCESS_TOKEN" : "xxxxx",
    }
    ret_val = env_variable_dict.get(key, None)
    return ret_val