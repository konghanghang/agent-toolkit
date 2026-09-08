-- 在已核对的目标权限库执行。先确认菜单唯一，以及 URL 是否已登记。
SELECT id, app_code, name, path
FROM t_menu
WHERE app_code = {{APP_CODE}}
  AND name = '{{MENU_NAME}}' AND path = '{{MENU_PATH}}';

SELECT id, menu_id, app_code, path, method, verify_required, action_type
FROM t_url
WHERE app_code = {{APP_CODE}} AND path = '{{URL_PATH}}' AND method = '{{METHOD}}';

-- MAX(id)+1 仅用于人工串行登记；菜单不唯一或 URL 已存在时插入 0 行。
INSERT INTO t_url (
    id, menu_id, old_id, url_name, app_code, path, method,
    create_time, last_update_by, create_by, last_update_time,
    verify_required, action_type, insert_by, last_modify_by
)
SELECT
    (SELECT COALESCE(MAX(a.id), 0) + 1 FROM t_url a),
    m.id, NULL, NULL, {{APP_CODE}}, '{{URL_PATH}}', '{{METHOD}}',
    NOW(), NULL, {{CREATE_BY}}, NULL, {{VERIFY_REQUIRED}}, {{ACTION_TYPE}}, NULL, NULL
FROM (
    SELECT MAX(id) AS id
    FROM t_menu
    WHERE app_code = {{APP_CODE}}
      AND name = '{{MENU_NAME}}' AND path = '{{MENU_PATH}}'
    HAVING COUNT(*) = 1
) m
WHERE NOT EXISTS (
    SELECT 1 FROM t_url u
    WHERE u.app_code = {{APP_CODE}}
      AND u.path = '{{URL_PATH}}' AND u.method = '{{METHOD}}'
);

-- 应唯一返回一行，并核对 menu_id、verify_required、action_type。
SELECT id, menu_id, app_code, path, method, verify_required, action_type
FROM t_url
WHERE app_code = {{APP_CODE}} AND path = '{{URL_PATH}}' AND method = '{{METHOD}}';
