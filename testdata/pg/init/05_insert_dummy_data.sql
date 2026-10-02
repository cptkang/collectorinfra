-- 자동 생성: testdata/pg/generate_full_schema.py
-- 타입 기반 더미 데이터 (테이블당 5행)
-- cmm_resource / core_config_prop 는 실데이터 보존을 위해 제외

TRUNCATE TABLE "polestar"."acc_acl";
INSERT INTO "polestar"."acc_acl" ("id", "auth_domain", "manageruserid", "ownerobjectuuid") VALUES
  (1, 'auth_domain_1', 'manageruserid_1', 'ownerobjectuuid_1'),
  (2, 'auth_domain_2', 'manageruserid_2', 'ownerobjectuuid_2'),
  (3, 'auth_domain_3', 'manageruserid_3', 'ownerobjectuuid_3'),
  (4, 'auth_domain_4', 'manageruserid_4', 'ownerobjectuuid_4'),
  (5, 'auth_domain_5', 'manageruserid_5', 'ownerobjectuuid_5');

TRUNCATE TABLE "polestar"."acc_acl_manager";
INSERT INTO "polestar"."acc_acl_manager" ("id", "mtime", "ownerobjectuuid") VALUES
  (1, '2026-06-01 09:00:00', 'ownerobjectuuid_1'),
  (2, '2026-06-02 09:00:00', 'ownerobjectuuid_2'),
  (3, '2026-06-03 09:00:00', 'ownerobjectuuid_3'),
  (4, '2026-06-04 09:00:00', 'ownerobjectuuid_4'),
  (5, '2026-06-05 09:00:00', 'ownerobjectuuid_5');

TRUNCATE TABLE "polestar"."acc_acl_manager_history";
INSERT INTO "polestar"."acc_acl_manager_history" ("id", "action", "group_id", "isinherited", "manager_name", "manager_type", "modifiedby", "mtime", "resource_id") VALUES
  (1, 'action_1', 1, 'isinherited_1', 'manager_name_1', 'manager_type_1', 'modifiedby_1', 'mtime_1', 1),
  (2, 'action_2', 2, 'isinherited_2', 'manager_name_2', 'manager_type_2', 'modifiedby_2', 'mtime_2', 2),
  (3, 'action_3', 3, 'isinherited_3', 'manager_name_3', 'manager_type_3', 'modifiedby_3', 'mtime_3', 3),
  (4, 'action_4', 4, 'isinherited_4', 'manager_name_4', 'manager_type_4', 'modifiedby_4', 'mtime_4', 4),
  (5, 'action_5', 5, 'isinherited_5', 'manager_name_5', 'manager_type_5', 'modifiedby_5', 'mtime_5', 5);

TRUNCATE TABLE "polestar"."acc_acl_perm";
INSERT INTO "polestar"."acc_acl_perm" ("id", "action_part", "acl_id", "role_id") VALUES
  (1, 1, 1, 1),
  (2, 2, 2, 2),
  (3, 3, 3, 3),
  (4, 4, 4, 4),
  (5, 5, 5, 5);

TRUNCATE TABLE "polestar"."acc_acl_resource_manager_list";
INSERT INTO "polestar"."acc_acl_resource_manager_list" ("aclforresourcemanager_id", "type_id", "manageruserids") VALUES
  (1, 1, 'manageruserids_1'),
  (2, 2, 'manageruserids_2'),
  (3, 3, 'manageruserids_3'),
  (4, 4, 'manageruserids_4'),
  (5, 5, 'manageruserids_5');

TRUNCATE TABLE "polestar"."acc_acl_resource_manager_type";
INSERT INTO "polestar"."acc_acl_resource_manager_type" ("id", "deletable", "name") VALUES
  (1, 1, 'name_1'),
  (2, 2, 'name_2'),
  (3, 3, 'name_3'),
  (4, 4, 'name_4'),
  (5, 5, 'name_5');

TRUNCATE TABLE "polestar"."acc_acl_resource_managers";
INSERT INTO "polestar"."acc_acl_resource_managers" ("acl_id", "manageruserids") VALUES
  (1, 'manageruserids_1'),
  (2, 'manageruserids_2'),
  (3, 'manageruserids_3'),
  (4, 'manageruserids_4'),
  (5, 'manageruserids_5');

TRUNCATE TABLE "polestar"."acc_acl_resource_user_group";
INSERT INTO "polestar"."acc_acl_resource_user_group" ("acl_id", "group_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."acc_audit_log";
INSERT INTO "polestar"."acc_audit_log" ("id", "action", "auditdetail", "audittime", "authdomain", "entityid", "entitytype", "host", "name", "operation", "userid") VALUES
  (1, 1, 'auditdetail_1', '2026-06-01 09:00:00', 'authdomain_1', 'entityid_1', 'entitytype_1', 'host_1', 'name_1', 'operation_1', 'userid_1'),
  (2, 2, 'auditdetail_2', '2026-06-02 09:00:00', 'authdomain_2', 'entityid_2', 'entitytype_2', 'host_2', 'name_2', 'operation_2', 'userid_2'),
  (3, 3, 'auditdetail_3', '2026-06-03 09:00:00', 'authdomain_3', 'entityid_3', 'entitytype_3', 'host_3', 'name_3', 'operation_3', 'userid_3'),
  (4, 4, 'auditdetail_4', '2026-06-04 09:00:00', 'authdomain_4', 'entityid_4', 'entitytype_4', 'host_4', 'name_4', 'operation_4', 'userid_4'),
  (5, 5, 'auditdetail_5', '2026-06-05 09:00:00', 'authdomain_5', 'entityid_5', 'entitytype_5', 'host_5', 'name_5', 'operation_5', 'userid_5');

TRUNCATE TABLE "polestar"."acc_auth_domain";
INSERT INTO "polestar"."acc_auth_domain" ("authdomain", "description", "displayname") VALUES
  ('authdomain_1', 'description_1', 'displayname_1'),
  ('authdomain_2', 'description_2', 'displayname_2'),
  ('authdomain_3', 'description_3', 'displayname_3'),
  ('authdomain_4', 'description_4', 'displayname_4'),
  ('authdomain_5', 'description_5', 'displayname_5');

TRUNCATE TABLE "polestar"."acc_login_log";
INSERT INTO "polestar"."acc_login_log" ("id", "loginsuccess", "logintime", "logouttime", "managerid", "message", "userip", "userid") VALUES
  (1, 1, '2026-06-01 09:00:00', '2026-06-01 09:00:00', 'managerid_1', 'message_1', 'userip_1', 'userid_1'),
  (2, 2, '2026-06-02 09:00:00', '2026-06-02 09:00:00', 'managerid_2', 'message_2', 'userip_2', 'userid_2'),
  (3, 3, '2026-06-03 09:00:00', '2026-06-03 09:00:00', 'managerid_3', 'message_3', 'userip_3', 'userid_3'),
  (4, 4, '2026-06-04 09:00:00', '2026-06-04 09:00:00', 'managerid_4', 'message_4', 'userip_4', 'userid_4'),
  (5, 5, '2026-06-05 09:00:00', '2026-06-05 09:00:00', 'managerid_5', 'message_5', 'userip_5', 'userid_5');

TRUNCATE TABLE "polestar"."acc_manager_group";
INSERT INTO "polestar"."acc_manager_group" ("id", "ownerobjectuuid") VALUES
  (1, 'ownerobjectuuid_1'),
  (2, 'ownerobjectuuid_2'),
  (3, 'ownerobjectuuid_3'),
  (4, 'ownerobjectuuid_4'),
  (5, 'ownerobjectuuid_5');

TRUNCATE TABLE "polestar"."acc_manager_group_list";
INSERT INTO "polestar"."acc_manager_group_list" ("aclforresourcemanagergroup_id", "managergroupids") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."acc_role";
INSERT INTO "polestar"."acc_role" ("id", "description", "master", "name") VALUES
  (1, 'description_1', 1, 'name_1'),
  (2, 'description_2', 2, 'name_2'),
  (3, 'description_3', 3, 'name_3'),
  (4, 'description_4', 4, 'name_4'),
  (5, 'description_5', 5, 'name_5');

TRUNCATE TABLE "polestar"."acc_rsakey";
INSERT INTO "polestar"."acc_rsakey" ("id", "ctime", "privatekeyexponent", "privatekeymodulus", "uuid") VALUES
  (1, 1, 'privatekeyexponent_1', 'privatekeymodulus_1', 'uuid_1'),
  (2, 2, 'privatekeyexponent_2', 'privatekeymodulus_2', 'uuid_2'),
  (3, 3, 'privatekeyexponent_3', 'privatekeymodulus_3', 'uuid_3'),
  (4, 4, 'privatekeyexponent_4', 'privatekeymodulus_4', 'uuid_4'),
  (5, 5, 'privatekeyexponent_5', 'privatekeymodulus_5', 'uuid_5');

TRUNCATE TABLE "polestar"."acc_static_perm";
INSERT INTO "polestar"."acc_static_perm" ("id", "action_part", "auth_domain", "role_id") VALUES
  (1, 1, 'auth_domain_1', 1),
  (2, 2, 'auth_domain_2', 2),
  (3, 3, 'auth_domain_3', 3),
  (4, 4, 'auth_domain_4', 4),
  (5, 5, 'auth_domain_5', 5);

TRUNCATE TABLE "polestar"."acc_user";
INSERT INTO "polestar"."acc_user" ("id", "aclips", "alarmattentionpopup", "alarmattentionrepeat", "alarmattentionrepeattime", "alarmattentionsound", "alarmbgcolor", "alarmclearpopup", "alarmclearsound", "alarmconsolerowsperpage", "alarmcriticalpopup", "alarmcriticalrepeat", "alarmcriticalrepeattime", "alarmcriticalsound", "alarmerrorpopup", "alarmerrorrepeat", "alarmerrorrepeattime", "alarmerrorsound", "alarmfatalpopup", "alarmfatalrepeat", "alarmfatalrepeattime", "alarmfatalsound", "alarmseverityfilter", "alarmstatusackedreptstop", "alarmstatusfinishedreptstop", "alarmstatusnotackreptstop", "alarmstatusprocessingreptstop", "alarmtroublepopup", "alarmtroublerepeat", "alarmtroublerepeattime", "alarmtroublesound", "ctime", "company", "department", "description", "email", "enable_user_noti_status", "eventconsolerowsperpage", "lastlogondate", "lastpasswordchanged", "locked", "logcollector", "maintenanceenabled", "messengerid", "notificationstatus", "optlock", "password", "phonenumber", "popupenabled", "receiveemail", "receivesms", "repeatenabled", "repeatstopenabled", "requiredpasswordchange", "soundenabled", "ssoexcepted", "userlang", "usertype", "username", "exceptexpire", "popuptime") VALUES
  ('id_1', 'aclips_1', 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 'alarmseverityfilter_1', 1, 1, 1, 1, 1, 1, 1, 1, 1, 'company_1', 'department_1', 'description_1', 'email_1', 1, 1, 1, 1, 1, 1, 1, 'messengerid_1', 1, 1, 'password_1', 'phonenumber_1', 1, 1, 1, 1, 1, 1, 1, 1, 'userlang_1', 'usertype_1', 'username_1', 1, 1),
  ('id_2', 'aclips_2', 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 'alarmseverityfilter_2', 2, 2, 2, 2, 2, 2, 2, 2, 2, 'company_2', 'department_2', 'description_2', 'email_2', 2, 2, 2, 2, 2, 2, 2, 'messengerid_2', 2, 2, 'password_2', 'phonenumber_2', 2, 2, 2, 2, 2, 2, 2, 2, 'userlang_2', 'usertype_2', 'username_2', 2, 2),
  ('id_3', 'aclips_3', 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 'alarmseverityfilter_3', 3, 3, 3, 3, 3, 3, 3, 3, 3, 'company_3', 'department_3', 'description_3', 'email_3', 3, 3, 3, 3, 3, 3, 3, 'messengerid_3', 3, 3, 'password_3', 'phonenumber_3', 3, 3, 3, 3, 3, 3, 3, 3, 'userlang_3', 'usertype_3', 'username_3', 3, 3),
  ('id_4', 'aclips_4', 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 'alarmseverityfilter_4', 4, 4, 4, 4, 4, 4, 4, 4, 4, 'company_4', 'department_4', 'description_4', 'email_4', 4, 4, 4, 4, 4, 4, 4, 'messengerid_4', 4, 4, 'password_4', 'phonenumber_4', 4, 4, 4, 4, 4, 4, 4, 4, 'userlang_4', 'usertype_4', 'username_4', 4, 4),
  ('id_5', 'aclips_5', 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 'alarmseverityfilter_5', 5, 5, 5, 5, 5, 5, 5, 5, 5, 'company_5', 'department_5', 'description_5', 'email_5', 5, 5, 5, 5, 5, 5, 5, 'messengerid_5', 5, 5, 'password_5', 'phonenumber_5', 5, 5, 5, 5, 5, 5, 5, 5, 'userlang_5', 'usertype_5', 'username_5', 5, 5);

TRUNCATE TABLE "polestar"."acc_user_group";
INSERT INTO "polestar"."acc_user_group" ("id", "description", "master", "name") VALUES
  (1, 'description_1', 1, 'name_1'),
  (2, 'description_2', 2, 'name_2'),
  (3, 'description_3', 3, 'name_3'),
  (4, 'description_4', 4, 'name_4'),
  (5, 'description_5', 5, 'name_5');

TRUNCATE TABLE "polestar"."acc_user_password_log";
INSERT INTO "polestar"."acc_user_password_log" ("user_id", "change_time", "password") VALUES
  ('user_id_1', 1, 'password_1'),
  ('user_id_2', 2, 'password_2'),
  ('user_id_3', 3, 'password_3'),
  ('user_id_4', 4, 'password_4'),
  ('user_id_5', 5, 'password_5');

TRUNCATE TABLE "polestar"."acc_user_role";
INSERT INTO "polestar"."acc_user_role" ("user_id", "role_id") VALUES
  ('user_id_1', 1),
  ('user_id_2', 2),
  ('user_id_3', 3),
  ('user_id_4', 4),
  ('user_id_5', 5);

TRUNCATE TABLE "polestar"."acc_user_url";
INSERT INTO "polestar"."acc_user_url" ("id", "acl_id", "columnorder", "description", "height", "httpurl", "image", "imagefilebyte", "imagefilename", "name", "url_type", "userid", "uuid", "width") VALUES
  (1, 1, 1, 'description_1', 1, 'httpurl_1', 'image_1', 1, 'imagefilename_1', 'name_1', 'url_type_1', 'userid_1', 'uuid_1', 1),
  (2, 2, 2, 'description_2', 2, 'httpurl_2', 'image_2', 2, 'imagefilename_2', 'name_2', 'url_type_2', 'userid_2', 'uuid_2', 2),
  (3, 3, 3, 'description_3', 3, 'httpurl_3', 'image_3', 3, 'imagefilename_3', 'name_3', 'url_type_3', 'userid_3', 'uuid_3', 3),
  (4, 4, 4, 'description_4', 4, 'httpurl_4', 'image_4', 4, 'imagefilename_4', 'name_4', 'url_type_4', 'userid_4', 'uuid_4', 4),
  (5, 5, 5, 'description_5', 5, 'httpurl_5', 'image_5', 5, 'imagefilename_5', 'name_5', 'url_type_5', 'userid_5', 'uuid_5', 5);

TRUNCATE TABLE "polestar"."acc_user_working_hour";
INSERT INTO "polestar"."acc_user_working_hour" ("user_id", "day_of_week", "working_hour") VALUES
  ('user_id_1', 1, 'working_hour_1'),
  ('user_id_2', 2, 'working_hour_2'),
  ('user_id_3', 3, 'working_hour_3'),
  ('user_id_4', 4, 'working_hour_4'),
  ('user_id_5', 5, 'working_hour_5');

TRUNCATE TABLE "polestar"."acc_usergroup_user";
INSERT INTO "polestar"."acc_usergroup_user" ("group_id", "user_id") VALUES
  (1, 'user_id_1'),
  (2, 'user_id_2'),
  (3, 'user_id_3'),
  (4, 'user_id_4'),
  (5, 'user_id_5');

TRUNCATE TABLE "polestar"."anomaly_model_cur";
INSERT INTO "polestar"."anomaly_model_cur" ("anomaly_key", "distributionmodel", "end_time", "expired_time", "learning_data_size", "period_mode", "model_json", "start_time") VALUES
  ('anomaly_key_1', 1, 1, 1, 1, 'period_mode_1', 'model_json_1', 1),
  ('anomaly_key_2', 2, 2, 2, 2, 'period_mode_2', 'model_json_2', 2),
  ('anomaly_key_3', 3, 3, 3, 3, 'period_mode_3', 'model_json_3', 3),
  ('anomaly_key_4', 4, 4, 4, 4, 'period_mode_4', 'model_json_4', 4),
  ('anomaly_key_5', 5, 5, 5, 5, 'period_mode_5', 'model_json_5', 5);

TRUNCATE TABLE "polestar"."aws_billing_day_forecast";
INSERT INTO "polestar"."aws_billing_day_forecast" ("id", "cost", "forecast_time", "user_account_id") VALUES
  (1, 1.5, 'forecast_time_1', 'user_account_id_1'),
  (2, 2.5, 'forecast_time_2', 'user_account_id_2'),
  (3, 3.5, 'forecast_time_3', 'user_account_id_3'),
  (4, 4.5, 'forecast_time_4', 'user_account_id_4'),
  (5, 5.5, 'forecast_time_5', 'user_account_id_5');

TRUNCATE TABLE "polestar"."aws_billing_report";
INSERT INTO "polestar"."aws_billing_report" ("account_id", "access_key", "athena_database_name", "athena_table_name", "bucket_name", "index_name", "region", "report_name", "secret_access_key", "stack_name", "status", "uuid") VALUES
  ('account_id_1', 'access_key_1', 'athena_database_name_1', 'athena_table_name_1', 'bucket_name_1', 'index_name_1', 'region_1', 'report_name_1', 'secret_access_key_1', 'stack_name_1', 'status_1', 'uuid_1'),
  ('account_id_2', 'access_key_2', 'athena_database_name_2', 'athena_table_name_2', 'bucket_name_2', 'index_name_2', 'region_2', 'report_name_2', 'secret_access_key_2', 'stack_name_2', 'status_2', 'uuid_2'),
  ('account_id_3', 'access_key_3', 'athena_database_name_3', 'athena_table_name_3', 'bucket_name_3', 'index_name_3', 'region_3', 'report_name_3', 'secret_access_key_3', 'stack_name_3', 'status_3', 'uuid_3'),
  ('account_id_4', 'access_key_4', 'athena_database_name_4', 'athena_table_name_4', 'bucket_name_4', 'index_name_4', 'region_4', 'report_name_4', 'secret_access_key_4', 'stack_name_4', 'status_4', 'uuid_4'),
  ('account_id_5', 'access_key_5', 'athena_database_name_5', 'athena_table_name_5', 'bucket_name_5', 'index_name_5', 'region_5', 'report_name_5', 'secret_access_key_5', 'stack_name_5', 'status_5', 'uuid_5');

TRUNCATE TABLE "polestar"."aws_billing_user";
INSERT INTO "polestar"."aws_billing_user" ("account_id", "account_name", "dtime", "current_month_forecast", "last_collection_elapsed_time", "last_collection_time", "is_managed", "start_date", "master_account_id", "aws_resource_id") VALUES
  ('account_id_1', 'account_name_1', 1, 1.5, 1.5, 1, 1, 1, 'master_account_id_1', 1),
  ('account_id_2', 'account_name_2', 2, 2.5, 2.5, 2, 2, 2, 'master_account_id_2', 2),
  ('account_id_3', 'account_name_3', 3, 3.5, 3.5, 3, 3, 3, 'master_account_id_3', 3),
  ('account_id_4', 'account_name_4', 4, 4.5, 4.5, 4, 4, 4, 'master_account_id_4', 4),
  ('account_id_5', 'account_name_5', 5, 5.5, 5.5, 5, 5, 5, 'master_account_id_5', 5);

TRUNCATE TABLE "polestar"."aws_cloud_service";
INSERT INTO "polestar"."aws_cloud_service" ("id", "description", "name") VALUES
  ('id_1', 'description_1', 'name_1'),
  ('id_2', 'description_2', 'name_2'),
  ('id_3', 'description_3', 'name_3'),
  ('id_4', 'description_4', 'name_4'),
  ('id_5', 'description_5', 'name_5');

TRUNCATE TABLE "polestar"."aws_region";
INSERT INTO "polestar"."aws_region" ("id", "description", "name") VALUES
  ('id_1', 'description_1', 'name_1'),
  ('id_2', 'description_2', 'name_2'),
  ('id_3', 'description_3', 'name_3'),
  ('id_4', 'description_4', 'name_4'),
  ('id_5', 'description_5', 'name_5');

TRUNCATE TABLE "polestar"."azure_cloud_service";
INSERT INTO "polestar"."azure_cloud_service" ("id", "description", "name") VALUES
  ('id_1', 'description_1', 'name_1'),
  ('id_2', 'description_2', 'name_2'),
  ('id_3', 'description_3', 'name_3'),
  ('id_4', 'description_4', 'name_4'),
  ('id_5', 'description_5', 'name_5');

TRUNCATE TABLE "polestar"."biz_base_info";
INSERT INTO "polestar"."biz_base_info" ("resource_type_name", "business_resource_add", "business_tree_view", "target_resource_type", "businesslayer_type") VALUES
  ('resource_type_name_1', 1, 1, 1, 'businesslayer_type_1'),
  ('resource_type_name_2', 2, 2, 2, 'businesslayer_type_2'),
  ('resource_type_name_3', 3, 3, 3, 'businesslayer_type_3'),
  ('resource_type_name_4', 4, 4, 4, 'businesslayer_type_4'),
  ('resource_type_name_5', 5, 5, 5, 'businesslayer_type_5');

TRUNCATE TABLE "polestar"."biz_health";
INSERT INTO "polestar"."biz_health" ("id", "alarmseverity", "ctime", "ctimestamp", "conditionlogtext", "resourcestatus", "definition_id", "resource_id") VALUES
  (1, 1, '2026-06-01 09:00:00', 1, 'conditionlogtext_1', 1, 1, 1),
  (2, 2, '2026-06-02 09:00:00', 2, 'conditionlogtext_2', 2, 2, 2),
  (3, 3, '2026-06-03 09:00:00', 3, 'conditionlogtext_3', 3, 3, 3),
  (4, 4, '2026-06-04 09:00:00', 4, 'conditionlogtext_4', 4, 4, 4),
  (5, 5, '2026-06-05 09:00:00', 5, 'conditionlogtext_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."biz_health_active";
INSERT INTO "polestar"."biz_health_active" ("biz_health_id", "alarmseverity", "ctime", "ctimestamp", "conditionlogtext", "definition_id", "resource_id") VALUES
  (1, 1, '2026-06-01 09:00:00', 1, 'conditionlogtext_1', 1, 1),
  (2, 2, '2026-06-02 09:00:00', 2, 'conditionlogtext_2', 2, 2),
  (3, 3, '2026-06-03 09:00:00', 3, 'conditionlogtext_3', 3, 3),
  (4, 4, '2026-06-04 09:00:00', 4, 'conditionlogtext_4', 4, 4),
  (5, 5, '2026-06-05 09:00:00', 5, 'conditionlogtext_5', 5, 5);

TRUNCATE TABLE "polestar"."biz_health_con_log";
INSERT INTO "polestar"."biz_health_con_log" ("businesshealth_id", "ctime", "conditionlogtext", "sourcevalue") VALUES
  (1, '2026-06-01 09:00:00', 'conditionlogtext_1', 'sourcevalue_1'),
  (2, '2026-06-02 09:00:00', 'conditionlogtext_2', 'sourcevalue_2'),
  (3, '2026-06-03 09:00:00', 'conditionlogtext_3', 'sourcevalue_3'),
  (4, '2026-06-04 09:00:00', 'conditionlogtext_4', 'sourcevalue_4'),
  (5, '2026-06-05 09:00:00', 'conditionlogtext_5', 'sourcevalue_5');

TRUNCATE TABLE "polestar"."biz_health_def";
INSERT INTO "polestar"."biz_health_def" ("dtype", "id", "businesshealthcoverage", "is_deleted", "enabled", "mtime", "name", "alarmseverity", "conditiontext", "description", "matchexpression", "resource_id") VALUES
  ('dtype_1', 1, 'businesshealthcoverage_1', 1, 1, '2026-06-01 09:00:00', 'name_1', 1, 'conditiontext_1', 'description_1', 1, 1),
  ('dtype_2', 2, 'businesshealthcoverage_2', 2, 2, '2026-06-02 09:00:00', 'name_2', 2, 'conditiontext_2', 'description_2', 2, 2),
  ('dtype_3', 3, 'businesshealthcoverage_3', 3, 3, '2026-06-03 09:00:00', 'name_3', 3, 'conditiontext_3', 'description_3', 3, 3),
  ('dtype_4', 4, 'businesshealthcoverage_4', 4, 4, '2026-06-04 09:00:00', 'name_4', 4, 'conditiontext_4', 'description_4', 4, 4),
  ('dtype_5', 5, 'businesshealthcoverage_5', 5, 5, '2026-06-05 09:00:00', 'name_5', 5, 'conditiontext_5', 'description_5', 5, 5);

TRUNCATE TABLE "polestar"."biz_health_def_con";
INSERT INTO "polestar"."biz_health_def_con" ("dtype", "id", "time_stamp", "threshold_numeric", "threshold_numeric2", "operator", "units", "threshold_avail", "calculation_method", "threshold_string", "targetfunction", "target_platform", "definition_id", "biz_measurement_id", "measurement_def_id", "target_resource_id") VALUES
  ('dtype_1', 'id_1', 1, 1.5, 1.5, 1, 'units_1', 'threshold_avail_1', 1, 'threshold_string_1', 'targetfunction_1', 'target_platform_1', 1, 'biz_measurement_id_1', 1, 1),
  ('dtype_2', 'id_2', 2, 2.5, 2.5, 2, 'units_2', 'threshold_avail_2', 2, 'threshold_string_2', 'targetfunction_2', 'target_platform_2', 2, 'biz_measurement_id_2', 2, 2),
  ('dtype_3', 'id_3', 3, 3.5, 3.5, 3, 'units_3', 'threshold_avail_3', 3, 'threshold_string_3', 'targetfunction_3', 'target_platform_3', 3, 'biz_measurement_id_3', 3, 3),
  ('dtype_4', 'id_4', 4, 4.5, 4.5, 4, 'units_4', 'threshold_avail_4', 4, 'threshold_string_4', 'targetfunction_4', 'target_platform_4', 4, 'biz_measurement_id_4', 4, 4),
  ('dtype_5', 'id_5', 5, 5.5, 5.5, 5, 'units_5', 'threshold_avail_5', 5, 'threshold_string_5', 'targetfunction_5', 'target_platform_5', 5, 'biz_measurement_id_5', 5, 5);

TRUNCATE TABLE "polestar"."biz_health_def_con_avail_res";
INSERT INTO "polestar"."biz_health_def_con_avail_res" ("biz_health_def_con_avail_id", "resource_id") VALUES
  ('biz_health_def_con_avail_id_1', 1),
  ('biz_health_def_con_avail_id_2', 2),
  ('biz_health_def_con_avail_id_3', 3),
  ('biz_health_def_con_avail_id_4', 4),
  ('biz_health_def_con_avail_id_5', 5);

TRUNCATE TABLE "polestar"."biz_layer";
INSERT INTO "polestar"."biz_layer" ("type", "display_name", "layer_level") VALUES
  ('type_1', 'display_name_1', 1),
  ('type_2', 'display_name_2', 2),
  ('type_3', 'display_name_3', 3),
  ('type_4', 'display_name_4', 4),
  ('type_5', 'display_name_5', 5);

TRUNCATE TABLE "polestar"."biz_m_target";
INSERT INTO "polestar"."biz_m_target" ("biz_m_id", "resource_id") VALUES
  ('biz_m_id_1', 1),
  ('biz_m_id_2', 2),
  ('biz_m_id_3', 3),
  ('biz_m_id_4', 4),
  ('biz_m_id_5', 5);

TRUNCATE TABLE "polestar"."biz_map";
INSERT INTO "polestar"."biz_map" ("id") VALUES
  (1),
  (2),
  (3),
  (4),
  (5);

TRUNCATE TABLE "polestar"."biz_map_link";
INSERT INTO "polestar"."biz_map_link" ("id", "ctime", "dtime", "dependency_key", "directiontype", "port", "map_id", "source_node_id", "source_resource_id", "target_node_id", "target_resource_id") VALUES
  (1, 1, 1, 'dependency_key_1', 'directiontype_1', 'port_1', 1, 1, 1, 1, 1),
  (2, 2, 2, 'dependency_key_2', 'directiontype_2', 'port_2', 2, 2, 2, 2, 2),
  (3, 3, 3, 'dependency_key_3', 'directiontype_3', 'port_3', 3, 3, 3, 3, 3),
  (4, 4, 4, 'dependency_key_4', 'directiontype_4', 'port_4', 4, 4, 4, 4, 4),
  (5, 5, 5, 'dependency_key_5', 'directiontype_5', 'port_5', 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."biz_measurement";
INSERT INTO "polestar"."biz_measurement" ("id", "enabled", "targetfunction", "resource_id", "source_id") VALUES
  ('id_1', 1, 'targetfunction_1', 1, 1),
  ('id_2', 2, 'targetfunction_2', 2, 2),
  ('id_3', 3, 'targetfunction_3', 3, 3),
  ('id_4', 4, 'targetfunction_4', 4, 4),
  ('id_5', 5, 'targetfunction_5', 5, 5);

TRUNCATE TABLE "polestar"."biz_measurement_src";
INSERT INTO "polestar"."biz_measurement_src" ("id", "aggregatefuntion", "defaultms", "description", "displaytype", "expression_str", "name", "scaletype", "scalevalue", "uuid", "visibility", "measurementdefinition_id") VALUES
  (1, 'aggregatefuntion_1', 1, 'description_1', 'displaytype_1', 'expression_str_1', 'name_1', 'scaletype_1', 1, 'uuid_1', 1, 1),
  (2, 'aggregatefuntion_2', 2, 'description_2', 'displaytype_2', 'expression_str_2', 'name_2', 'scaletype_2', 2, 'uuid_2', 2, 2),
  (3, 'aggregatefuntion_3', 3, 'description_3', 'displaytype_3', 'expression_str_3', 'name_3', 'scaletype_3', 3, 'uuid_3', 3, 3),
  (4, 'aggregatefuntion_4', 4, 'description_4', 'displaytype_4', 'expression_str_4', 'name_4', 'scaletype_4', 4, 'uuid_4', 4, 4),
  (5, 'aggregatefuntion_5', 5, 'description_5', 'displaytype_5', 'expression_str_5', 'name_5', 'scaletype_5', 5, 'uuid_5', 5, 5);

TRUNCATE TABLE "polestar"."biz_resource";
INSERT INTO "polestar"."biz_resource" ("id", "add_type", "ctime", "chartmeasurementdefinitionid_1", "chartmeasurementdefinitionid_2", "chartresourceid_1", "chartresourceid_2", "dtime", "location", "businessservice_id", "map_id", "systemresource_id") VALUES
  (1, 'add_type_1', 1, 1, 1, 1, 1, 1, 'location_1', 1, 1, 1),
  (2, 'add_type_2', 2, 2, 2, 2, 2, 2, 'location_2', 2, 2, 2),
  (3, 'add_type_3', 3, 3, 3, 3, 3, 3, 'location_3', 3, 3, 3),
  (4, 'add_type_4', 4, 4, 4, 4, 4, 4, 'location_4', 4, 4, 4),
  (5, 'add_type_5', 5, 5, 5, 5, 5, 5, 'location_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."bsn_business_hour";
INSERT INTO "polestar"."bsn_business_hour" ("schedule_id", "day_of_week", "from_hour", "to_hour", "is_working_day") VALUES
  (1, 1, 1, 1, 1),
  (2, 2, 2, 2, 2),
  (3, 3, 3, 3, 3),
  (4, 4, 4, 4, 4),
  (5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."bsn_business_schedule";
INSERT INTO "polestar"."bsn_business_schedule" ("id", "description", "name") VALUES
  (1, 'description_1', 'name_1'),
  (2, 'description_2', 'name_2'),
  (3, 'description_3', 'name_3'),
  (4, 'description_4', 'name_4'),
  (5, 'description_5', 'name_5');

TRUNCATE TABLE "polestar"."bsn_holiday";
INSERT INTO "polestar"."bsn_holiday" ("id", "day_of_month", "is_enabled", "f_date", "interval", "is_lunar", "month_of_year", "name", "uuid", "year_val", "schedule_id") VALUES
  (1, 1, 1, 'f_date_1', 1, 1, 1, 'name_1', 'uuid_1', 1, 1),
  (2, 2, 2, 'f_date_2', 2, 2, 2, 'name_2', 'uuid_2', 2, 2),
  (3, 3, 3, 'f_date_3', 3, 3, 3, 'name_3', 'uuid_3', 3, 3),
  (4, 4, 4, 'f_date_4', 4, 4, 4, 'name_4', 'uuid_4', 4, 4),
  (5, 5, 5, 'f_date_5', 5, 5, 5, 'name_5', 'uuid_5', 5, 5);

TRUNCATE TABLE "polestar"."business_topology_map";
INSERT INTO "polestar"."business_topology_map" ("id", "ctime", "iscreatedlink", "iscustom", "mapnamesuffix", "propname", "propresourcetype", "propvalue", "resource_id", "topologymap_id") VALUES
  (1, 1, 1, 1, 'mapnamesuffix_1', 'propname_1', 'propresourcetype_1', 'propvalue_1', 1, 1),
  (2, 2, 2, 2, 'mapnamesuffix_2', 'propname_2', 'propresourcetype_2', 'propvalue_2', 2, 2),
  (3, 3, 3, 3, 'mapnamesuffix_3', 'propname_3', 'propresourcetype_3', 'propvalue_3', 3, 3),
  (4, 4, 4, 4, 'mapnamesuffix_4', 'propname_4', 'propresourcetype_4', 'propvalue_4', 4, 4),
  (5, 5, 5, 5, 'mapnamesuffix_5', 'propname_5', 'propresourcetype_5', 'propvalue_5', 5, 5);

TRUNCATE TABLE "polestar"."cmm_account_info";
INSERT INTO "polestar"."cmm_account_info" ("account_key", "account_value") VALUES
  ('account_key_1', 'account_value_1'),
  ('account_key_2', 'account_value_2'),
  ('account_key_3', 'account_value_3'),
  ('account_key_4', 'account_value_4'),
  ('account_key_5', 'account_value_5');

TRUNCATE TABLE "polestar"."cmm_ad_community_list";
INSERT INTO "polestar"."cmm_ad_community_list" ("community_list", "communitys") VALUES
  (1, 'communitys_1'),
  (2, 'communitys_2'),
  (3, 'communitys_3'),
  (4, 'communitys_4'),
  (5, 'communitys_5');

TRUNCATE TABLE "polestar"."cmm_ad_result";
INSERT INTO "polestar"."cmm_ad_result" ("id", "discovered_time", "first_time", "host_name", "ip_address", "module_type", "response_avg", "response_cnt", "response_max", "response_min", "response_sum", "response_time", "discover_status", "success_time", "ad_schedule_id") VALUES
  ('id_1', 1, 1, 'host_name_1', 'ip_address_1', 'module_type_1', 1.5, 1, 1, 1, 1, 1, 'discover_status_1', 1, 1),
  ('id_2', 2, 2, 'host_name_2', 'ip_address_2', 'module_type_2', 2.5, 2, 2, 2, 2, 2, 'discover_status_2', 2, 2),
  ('id_3', 3, 3, 'host_name_3', 'ip_address_3', 'module_type_3', 3.5, 3, 3, 3, 3, 3, 'discover_status_3', 3, 3),
  ('id_4', 4, 4, 'host_name_4', 'ip_address_4', 'module_type_4', 4.5, 4, 4, 4, 4, 4, 'discover_status_4', 4, 4),
  ('id_5', 5, 5, 'host_name_5', 'ip_address_5', 'module_type_5', 5.5, 5, 5, 5, 5, 5, 'discover_status_5', 5, 5);

TRUNCATE TABLE "polestar"."cmm_ad_sche_job_data";
INSERT INTO "polestar"."cmm_ad_sche_job_data" ("id", "is_error", "is_error_msg", "last_completion_time", "last_discoverd_time", "request_count", "reponse_count", "ad_schedule_id") VALUES
  ('id_1', 1, 'is_error_msg_1', 1, 1, 1, 1, 1),
  ('id_2', 2, 'is_error_msg_2', 2, 2, 2, 2, 2),
  ('id_3', 3, 'is_error_msg_3', 3, 3, 3, 3, 3),
  ('id_4', 4, 'is_error_msg_4', 4, 4, 4, 4, 4),
  ('id_5', 5, 'is_error_msg_5', 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_ad_schedule";
INSERT INTO "polestar"."cmm_ad_schedule" ("dtype", "id", "is_auto_regist", "ctime", "description", "discoveryinterval", "from_ipaddress", "is_icmp_check", "mtime", "modifiedby", "module_type", "name", "optlock", "is_running", "starttime", "to_ipaddress", "zone_id", "authalgorithm", "authpassword", "contextname", "port", "privacyalgorithm", "privacypassword", "securelevel", "snmpversion", "usmuser", "group_resource_id") VALUES
  ('dtype_1', 1, 1, 1, 'description_1', 1, 1, 1, 1, 'modifiedby_1', 'module_type_1', 'name_1', 1, 1, 1, 1, 'zone_id_1', 'authalgorithm_1', 'authpassword_1', 'contextname_1', 1, 'privacyalgorithm_1', 'privacypassword_1', 'securelevel_1', 'snmpversion_1', 'usmuser_1', 1),
  ('dtype_2', 2, 2, 2, 'description_2', 2, 2, 2, 2, 'modifiedby_2', 'module_type_2', 'name_2', 2, 2, 2, 2, 'zone_id_2', 'authalgorithm_2', 'authpassword_2', 'contextname_2', 2, 'privacyalgorithm_2', 'privacypassword_2', 'securelevel_2', 'snmpversion_2', 'usmuser_2', 2),
  ('dtype_3', 3, 3, 3, 'description_3', 3, 3, 3, 3, 'modifiedby_3', 'module_type_3', 'name_3', 3, 3, 3, 3, 'zone_id_3', 'authalgorithm_3', 'authpassword_3', 'contextname_3', 3, 'privacyalgorithm_3', 'privacypassword_3', 'securelevel_3', 'snmpversion_3', 'usmuser_3', 3),
  ('dtype_4', 4, 4, 4, 'description_4', 4, 4, 4, 4, 'modifiedby_4', 'module_type_4', 'name_4', 4, 4, 4, 4, 'zone_id_4', 'authalgorithm_4', 'authpassword_4', 'contextname_4', 4, 'privacyalgorithm_4', 'privacypassword_4', 'securelevel_4', 'snmpversion_4', 'usmuser_4', 4),
  ('dtype_5', 5, 5, 5, 'description_5', 5, 5, 5, 5, 'modifiedby_5', 'module_type_5', 'name_5', 5, 5, 5, 5, 'zone_id_5', 'authalgorithm_5', 'authpassword_5', 'contextname_5', 5, 'privacyalgorithm_5', 'privacypassword_5', 'securelevel_5', 'snmpversion_5', 'usmuser_5', 5);

TRUNCATE TABLE "polestar"."cmm_alarm";
INSERT INTO "polestar"."cmm_alarm" ("id", "acktime", "acktimestamp", "ackuserid", "ackusername", "alarmseverity", "ctime", "ctimestamp", "conditionid", "conditionlogtext", "currentalarmstatus", "currentnotemessage", "currentuserid", "currentusername", "dtime", "dtimestamp", "resourcestatus", "totalcount", "definition_id", "master_definition_id", "prev_alarm_id", "resource_id", "root_alarm_id", "maintenancefilteringjobid") VALUES
  (1, '2026-06-01 09:00:00', 1, 'ackuserid_1', 'ackusername_1', 1, '2026-06-01 09:00:00', 1, 'conditionid_1', 'conditionlogtext_1', 'currentalarmstatus_1', 'currentnotemessage_1', 'currentuserid_1', 'currentusername_1', '2026-06-01 09:00:00', 1, 1, 1, 1, 1, 1, 1, 1, 1),
  (2, '2026-06-02 09:00:00', 2, 'ackuserid_2', 'ackusername_2', 2, '2026-06-02 09:00:00', 2, 'conditionid_2', 'conditionlogtext_2', 'currentalarmstatus_2', 'currentnotemessage_2', 'currentuserid_2', 'currentusername_2', '2026-06-02 09:00:00', 2, 2, 2, 2, 2, 2, 2, 2, 2),
  (3, '2026-06-03 09:00:00', 3, 'ackuserid_3', 'ackusername_3', 3, '2026-06-03 09:00:00', 3, 'conditionid_3', 'conditionlogtext_3', 'currentalarmstatus_3', 'currentnotemessage_3', 'currentuserid_3', 'currentusername_3', '2026-06-03 09:00:00', 3, 3, 3, 3, 3, 3, 3, 3, 3),
  (4, '2026-06-04 09:00:00', 4, 'ackuserid_4', 'ackusername_4', 4, '2026-06-04 09:00:00', 4, 'conditionid_4', 'conditionlogtext_4', 'currentalarmstatus_4', 'currentnotemessage_4', 'currentuserid_4', 'currentusername_4', '2026-06-04 09:00:00', 4, 4, 4, 4, 4, 4, 4, 4, 4),
  (5, '2026-06-05 09:00:00', 5, 'ackuserid_5', 'ackusername_5', 5, '2026-06-05 09:00:00', 5, 'conditionid_5', 'conditionlogtext_5', 'currentalarmstatus_5', 'currentnotemessage_5', 'currentuserid_5', 'currentusername_5', '2026-06-05 09:00:00', 5, 5, 5, 5, 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_active";
INSERT INTO "polestar"."cmm_alarm_active" ("alarm_id", "accumulatedcount", "alarmseverity", "ctime", "ctimestamp", "conditionlogtext", "currentalarmstatus", "deletingscheduledtime", "resourcestatus", "definition_id", "resource_id", "maintenancefilteringjobid") VALUES
  (1, 1, 1, '2026-06-01 09:00:00', 1, 'conditionlogtext_1', 'currentalarmstatus_1', 1, 1, 1, 1, 1),
  (2, 2, 2, '2026-06-02 09:00:00', 2, 'conditionlogtext_2', 'currentalarmstatus_2', 2, 2, 2, 2, 2),
  (3, 3, 3, '2026-06-03 09:00:00', 3, 'conditionlogtext_3', 'currentalarmstatus_3', 3, 3, 3, 3, 3),
  (4, 4, 4, '2026-06-04 09:00:00', 4, 'conditionlogtext_4', 'currentalarmstatus_4', 4, 4, 4, 4, 4),
  (5, 5, 5, '2026-06-05 09:00:00', 5, 'conditionlogtext_5', 'currentalarmstatus_5', 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_complex_monitor";
INSERT INTO "polestar"."cmm_alarm_complex_monitor" ("id", "resource_id", "alarm_id") VALUES
  (1, 1, 1),
  (2, 2, 2),
  (3, 3, 3),
  (4, 4, 4),
  (5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_complex_monitor_sub";
INSERT INTO "polestar"."cmm_alarm_complex_monitor_sub" ("complexalarmmonitor_id", "subalarmids") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_con_log";
INSERT INTO "polestar"."cmm_alarm_con_log" ("alarm_id", "ctime", "conditionlogtext", "sourcevalue") VALUES
  (1, '2026-06-01 09:00:00', 'conditionlogtext_1', 'sourcevalue_1'),
  (2, '2026-06-02 09:00:00', 'conditionlogtext_2', 'sourcevalue_2'),
  (3, '2026-06-03 09:00:00', 'conditionlogtext_3', 'sourcevalue_3'),
  (4, '2026-06-04 09:00:00', 'conditionlogtext_4', 'sourcevalue_4'),
  (5, '2026-06-05 09:00:00', 'conditionlogtext_5', 'sourcevalue_5');

TRUNCATE TABLE "polestar"."cmm_alarm_console_filter";
INSERT INTO "polestar"."cmm_alarm_console_filter" ("user_id") VALUES
  ('user_id_1'),
  ('user_id_2'),
  ('user_id_3'),
  ('user_id_4'),
  ('user_id_5');

TRUNCATE TABLE "polestar"."cmm_alarm_console_filter_col";
INSERT INTO "polestar"."cmm_alarm_console_filter_col" ("user_id", "column_name") VALUES
  ('user_id_1', 1),
  ('user_id_2', 2),
  ('user_id_3', 3),
  ('user_id_4', 4),
  ('user_id_5', 5);

TRUNCATE TABLE "polestar"."cmm_alarm_def";
INSERT INTO "polestar"."cmm_alarm_def" ("dtype", "id", "is_deleted", "enabled", "mtime", "name", "alarmtimeout", "conditionlogtemplate", "conditiontext", "csvkey", "description", "notismsmessagetemplate", "pql", "resourcecoverage", "targetresourcetype_name", "timeoutseverity", "activealarmpolicy", "alarmseverity", "clearmatchexpression", "matchexpression", "maxalarmspermin", "resource_id", "coverageowner_id", "monitortemplate_id", "masterdefinition_id", "measurementdefinition_id", "isapply") VALUES
  ('dtype_1', 1, 1, 1, '2026-06-01 09:00:00', 'name_1', 1, 'conditionlogtemplate_1', 'conditiontext_1', 'csvkey_1', 'description_1', 'notismsmessagetemplate_1', 'pql_1', 'resourcecoverage_1', 'targetresourcetype_name_1', 1, 1, 1, 1, 1, 1, 1, 1, 'monitortemplate_id_1', 1, 1, 1),
  ('dtype_2', 2, 2, 2, '2026-06-02 09:00:00', 'name_2', 2, 'conditionlogtemplate_2', 'conditiontext_2', 'csvkey_2', 'description_2', 'notismsmessagetemplate_2', 'pql_2', 'resourcecoverage_2', 'targetresourcetype_name_2', 2, 2, 2, 2, 2, 2, 2, 2, 'monitortemplate_id_2', 2, 2, 2),
  ('dtype_3', 3, 3, 3, '2026-06-03 09:00:00', 'name_3', 3, 'conditionlogtemplate_3', 'conditiontext_3', 'csvkey_3', 'description_3', 'notismsmessagetemplate_3', 'pql_3', 'resourcecoverage_3', 'targetresourcetype_name_3', 3, 3, 3, 3, 3, 3, 3, 3, 'monitortemplate_id_3', 3, 3, 3),
  ('dtype_4', 4, 4, 4, '2026-06-04 09:00:00', 'name_4', 4, 'conditionlogtemplate_4', 'conditiontext_4', 'csvkey_4', 'description_4', 'notismsmessagetemplate_4', 'pql_4', 'resourcecoverage_4', 'targetresourcetype_name_4', 4, 4, 4, 4, 4, 4, 4, 4, 'monitortemplate_id_4', 4, 4, 4),
  ('dtype_5', 5, 5, 5, '2026-06-05 09:00:00', 'name_5', 5, 'conditionlogtemplate_5', 'conditiontext_5', 'csvkey_5', 'description_5', 'notismsmessagetemplate_5', 'pql_5', 'resourcecoverage_5', 'targetresourcetype_name_5', 5, 5, 5, 5, 5, 5, 5, 5, 'monitortemplate_id_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_def_c_con";
INSERT INTO "polestar"."cmm_alarm_def_c_con" ("dtype", "id", "time_stamp", "threshold_numeric", "threshold_numeric2", "operator", "units", "baselinecalctype", "baselinetype", "baseline_occurrences", "thresholdvaluetype", "count", "detailpattern", "eventseverity", "eventsourcepattern", "is_include_cr", "is_negate_detail", "is_negate_source", "severity_operator", "time_range", "frozen_eval_count", "goal_time", "goaltimeunit", "goal_value", "goal_period", "change_occurrences", "thresholdtype", "oob_occurrences", "canonicalpath", "pql", "threshold_long", "threshold_long_2", "alarmseverity", "subalarmcount", "timerange", "threshold_avail", "dampeningtype", "evaluations", "threshold_occurrences", "threshold_string", "clear_definition_id", "definition_id", "measurement_def_id", "property_def_id", "subalarmdefinition_id", "eventtype") VALUES
  ('dtype_1', 1, 1, 1.5, 1.5, 1, 'units_1', 'baselinecalctype_1', 'baselinetype_1', 1, 'thresholdvaluetype_1', 1, 'detailpattern_1', 1, 'eventsourcepattern_1', 1, 1, 1, 1, 1, 1, 1, 'goaltimeunit_1', 1.5, 1, 1, 'thresholdtype_1', 1, 'canonicalpath_1', 'pql_1', 1, 1, 1, 1, 1, 'threshold_avail_1', 'dampeningtype_1', 1, 1, 'threshold_string_1', 1, 1, 1, 1, 1, 'eventtype_1'),
  ('dtype_2', 2, 2, 2.5, 2.5, 2, 'units_2', 'baselinecalctype_2', 'baselinetype_2', 2, 'thresholdvaluetype_2', 2, 'detailpattern_2', 2, 'eventsourcepattern_2', 2, 2, 2, 2, 2, 2, 2, 'goaltimeunit_2', 2.5, 2, 2, 'thresholdtype_2', 2, 'canonicalpath_2', 'pql_2', 2, 2, 2, 2, 2, 'threshold_avail_2', 'dampeningtype_2', 2, 2, 'threshold_string_2', 2, 2, 2, 2, 2, 'eventtype_2'),
  ('dtype_3', 3, 3, 3.5, 3.5, 3, 'units_3', 'baselinecalctype_3', 'baselinetype_3', 3, 'thresholdvaluetype_3', 3, 'detailpattern_3', 3, 'eventsourcepattern_3', 3, 3, 3, 3, 3, 3, 3, 'goaltimeunit_3', 3.5, 3, 3, 'thresholdtype_3', 3, 'canonicalpath_3', 'pql_3', 3, 3, 3, 3, 3, 'threshold_avail_3', 'dampeningtype_3', 3, 3, 'threshold_string_3', 3, 3, 3, 3, 3, 'eventtype_3'),
  ('dtype_4', 4, 4, 4.5, 4.5, 4, 'units_4', 'baselinecalctype_4', 'baselinetype_4', 4, 'thresholdvaluetype_4', 4, 'detailpattern_4', 4, 'eventsourcepattern_4', 4, 4, 4, 4, 4, 4, 4, 'goaltimeunit_4', 4.5, 4, 4, 'thresholdtype_4', 4, 'canonicalpath_4', 'pql_4', 4, 4, 4, 4, 4, 'threshold_avail_4', 'dampeningtype_4', 4, 4, 'threshold_string_4', 4, 4, 4, 4, 4, 'eventtype_4'),
  ('dtype_5', 5, 5, 5.5, 5.5, 5, 'units_5', 'baselinecalctype_5', 'baselinetype_5', 5, 'thresholdvaluetype_5', 5, 'detailpattern_5', 5, 'eventsourcepattern_5', 5, 5, 5, 5, 5, 5, 5, 'goaltimeunit_5', 5.5, 5, 5, 'thresholdtype_5', 5, 'canonicalpath_5', 'pql_5', 5, 5, 5, 5, 5, 'threshold_avail_5', 'dampeningtype_5', 5, 5, 'threshold_string_5', 5, 5, 5, 5, 5, 'eventtype_5');

TRUNCATE TABLE "polestar"."cmm_alarm_def_history";
INSERT INTO "polestar"."cmm_alarm_def_history" ("id", "alarmdefinitionmodifiedtime", "alarmdefinitionname", "ctime", "conditiontext", "crudtype", "is_deleted", "enabled", "mastermodifiedtime", "modifiedby", "is_proxy", "alarmdefinition_id", "masteralarmdefinition_id", "resource_id") VALUES
  (1, 1, 'alarmdefinitionname_1', 1, 'conditiontext_1', 'crudtype_1', 1, 1, 1, 'modifiedby_1', 1, 1, 1, 1),
  (2, 2, 'alarmdefinitionname_2', 2, 'conditiontext_2', 'crudtype_2', 2, 2, 2, 'modifiedby_2', 2, 2, 2, 2),
  (3, 3, 'alarmdefinitionname_3', 3, 'conditiontext_3', 'crudtype_3', 3, 3, 3, 'modifiedby_3', 3, 3, 3, 3),
  (4, 4, 'alarmdefinitionname_4', 4, 'conditiontext_4', 'crudtype_4', 4, 4, 4, 'modifiedby_4', 4, 4, 4, 4),
  (5, 5, 'alarmdefinitionname_5', 5, 'conditiontext_5', 'crudtype_5', 5, 5, 5, 'modifiedby_5', 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_def_noti";
INSERT INTO "polestar"."cmm_alarm_def_noti" ("id", "alarmseverity", "delaytimemin", "is_noti_clear_severity", "notimethod", "is_noti_only_managed", "notitarget", "severityoperator", "time_stamp", "definition_id") VALUES
  (1, 1, 1, 1, 'notimethod_1', 1, 'notitarget_1', 1, 1, 1),
  (2, 2, 2, 2, 'notimethod_2', 2, 'notitarget_2', 2, 2, 2),
  (3, 3, 3, 3, 'notimethod_3', 3, 'notitarget_3', 3, 3, 3),
  (4, 4, 4, 4, 'notimethod_4', 4, 'notitarget_4', 4, 4, 4),
  (5, 5, 5, 5, 'notimethod_5', 5, 'notitarget_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_def_noti_group";
INSERT INTO "polestar"."cmm_alarm_def_noti_group" ("alarmnotification_id", "targetgroups") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_def_noti_rmtype";
INSERT INTO "polestar"."cmm_alarm_def_noti_rmtype" ("alarmnotification_id", "targetresourcemanagertypes") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_def_noti_role";
INSERT INTO "polestar"."cmm_alarm_def_noti_role" ("alarmnotification_id", "targetroles") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_def_noti_user";
INSERT INTO "polestar"."cmm_alarm_def_noti_user" ("alarmnotification_id", "targetusers") VALUES
  (1, 'targetusers_1'),
  (2, 'targetusers_2'),
  (3, 'targetusers_3'),
  (4, 'targetusers_4'),
  (5, 'targetusers_5');

TRUNCATE TABLE "polestar"."cmm_alarm_def_s_con";
INSERT INTO "polestar"."cmm_alarm_def_s_con" ("id", "severity", "threshold_avail", "dampeningtype", "evaluations", "threshold_numeric", "threshold_numeric2", "occurrences", "operator", "threshold_string", "time_stamp", "units", "definition_id") VALUES
  (1, 1, 'threshold_avail_1', 'dampeningtype_1', 1, 1.5, 1.5, 1, 1, 'threshold_string_1', 1, 'units_1', 1),
  (2, 2, 'threshold_avail_2', 'dampeningtype_2', 2, 2.5, 2.5, 2, 2, 'threshold_string_2', 2, 'units_2', 2),
  (3, 3, 'threshold_avail_3', 'dampeningtype_3', 3, 3.5, 3.5, 3, 3, 'threshold_string_3', 3, 'units_3', 3),
  (4, 4, 'threshold_avail_4', 'dampeningtype_4', 4, 4.5, 4.5, 4, 4, 'threshold_string_4', 4, 'units_4', 4),
  (5, 5, 'threshold_avail_5', 'dampeningtype_5', 5, 5.5, 5.5, 5, 5, 'threshold_string_5', 5, 'units_5', 5);

TRUNCATE TABLE "polestar"."cmm_alarm_delay_noti";
INSERT INTO "polestar"."cmm_alarm_delay_noti" ("id", "delaynotitime", "notiid", "patternnotiid", "repeatcount", "is_repeatedly", "alarm_id") VALUES
  (1, 1, 1, 1, 1, 1, 1),
  (2, 2, 2, 2, 2, 2, 2),
  (3, 3, 3, 3, 3, 3, 3),
  (4, 4, 4, 4, 4, 4, 4),
  (5, 5, 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_fault_type";
INSERT INTO "polestar"."cmm_alarm_fault_type" ("fault_type_name", "fault_type_description") VALUES
  ('fault_type_name_1', 'fault_type_description_1'),
  ('fault_type_name_2', 'fault_type_description_2'),
  ('fault_type_name_3', 'fault_type_description_3'),
  ('fault_type_name_4', 'fault_type_description_4'),
  ('fault_type_name_5', 'fault_type_description_5');

TRUNCATE TABLE "polestar"."cmm_alarm_knowledge";
INSERT INTO "polestar"."cmm_alarm_knowledge" ("id", "attachedfile", "attachedfilename", "ctime", "faultcontent", "faulttypename", "mtime", "processcontent", "alarm_definition_id") VALUES
  (1, 1, 'attachedfilename_1', 1, 'faultcontent_1', 'faulttypename_1', 1, 'processcontent_1', 1),
  (2, 2, 'attachedfilename_2', 2, 'faultcontent_2', 'faulttypename_2', 2, 'processcontent_2', 2),
  (3, 3, 'attachedfilename_3', 3, 'faultcontent_3', 'faulttypename_3', 3, 'processcontent_3', 3),
  (4, 4, 'attachedfilename_4', 4, 'faultcontent_4', 'faulttypename_4', 4, 'processcontent_4', 4),
  (5, 5, 'attachedfilename_5', 5, 'faultcontent_5', 'faulttypename_5', 5, 'processcontent_5', 5);

TRUNCATE TABLE "polestar"."cmm_alarm_linkage_history";
INSERT INTO "polestar"."cmm_alarm_linkage_history" ("id", "alarmid", "linkagetype", "resultmessage", "rootalarmid", "sendtime", "success") VALUES
  (1, 1, 1, 'resultmessage_1', 1, '2026-06-01 09:00:00', 1),
  (2, 2, 2, 'resultmessage_2', 2, '2026-06-02 09:00:00', 2),
  (3, 3, 3, 'resultmessage_3', 3, '2026-06-03 09:00:00', 3),
  (4, 4, 4, 'resultmessage_4', 4, '2026-06-04 09:00:00', 4),
  (5, 5, 5, 'resultmessage_5', 5, '2026-06-05 09:00:00', 5);

TRUNCATE TABLE "polestar"."cmm_alarm_note";
INSERT INTO "polestar"."cmm_alarm_note" ("id", "alarmcause", "alarmstatus", "chargeuserid", "chargeusername", "mtime", "message", "userid", "username", "alarm_id", "alarm_definition_id") VALUES
  (1, 'alarmcause_1', 'alarmstatus_1', 'chargeuserid_1', 'chargeusername_1', '2026-06-01 09:00:00', 'message_1', 'userid_1', 'username_1', 1, 1),
  (2, 'alarmcause_2', 'alarmstatus_2', 'chargeuserid_2', 'chargeusername_2', '2026-06-02 09:00:00', 'message_2', 'userid_2', 'username_2', 2, 2),
  (3, 'alarmcause_3', 'alarmstatus_3', 'chargeuserid_3', 'chargeusername_3', '2026-06-03 09:00:00', 'message_3', 'userid_3', 'username_3', 3, 3),
  (4, 'alarmcause_4', 'alarmstatus_4', 'chargeuserid_4', 'chargeusername_4', '2026-06-04 09:00:00', 'message_4', 'userid_4', 'username_4', 4, 4),
  (5, 'alarmcause_5', 'alarmstatus_5', 'chargeuserid_5', 'chargeusername_5', '2026-06-05 09:00:00', 'message_5', 'userid_5', 'username_5', 5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_pattern_def_except";
INSERT INTO "polestar"."cmm_alarm_pattern_def_except" ("pattern_noti_def_id", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_pattern_def_target";
INSERT INTO "polestar"."cmm_alarm_pattern_def_target" ("pattern_noti_def_id", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_pattern_noti";
INSERT INTO "polestar"."cmm_alarm_pattern_noti" ("id", "alarmseverity", "delaytimemin", "is_noti_clear_severity", "notimethod", "is_noti_only_managed", "notitarget", "severityfornoti", "severityoperator", "time_stamp", "definition_id") VALUES
  (1, 1, 1, 1, 'notimethod_1', 1, 'notitarget_1', 'severityfornoti_1', 1, 1, 1),
  (2, 2, 2, 2, 'notimethod_2', 2, 'notitarget_2', 'severityfornoti_2', 2, 2, 2),
  (3, 3, 3, 3, 'notimethod_3', 3, 'notitarget_3', 'severityfornoti_3', 3, 3, 3),
  (4, 4, 4, 4, 'notimethod_4', 4, 'notitarget_4', 'severityfornoti_4', 4, 4, 4),
  (5, 5, 5, 5, 'notimethod_5', 5, 'notitarget_5', 'severityfornoti_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_pattern_noti_def";
INSERT INTO "polestar"."cmm_alarm_pattern_noti_def" ("id", "included_pattern", "message_template", "is_check_both", "description", "enabled", "excluded_pattern", "linkaged_id", "mtime", "name") VALUES
  (1, 'included_pattern_1', 'message_template_1', 1, 'description_1', 1, 'excluded_pattern_1', 'linkaged_id_1', '2026-06-01 09:00:00', 'name_1'),
  (2, 'included_pattern_2', 'message_template_2', 2, 'description_2', 2, 'excluded_pattern_2', 'linkaged_id_2', '2026-06-02 09:00:00', 'name_2'),
  (3, 'included_pattern_3', 'message_template_3', 3, 'description_3', 3, 'excluded_pattern_3', 'linkaged_id_3', '2026-06-03 09:00:00', 'name_3'),
  (4, 'included_pattern_4', 'message_template_4', 4, 'description_4', 4, 'excluded_pattern_4', 'linkaged_id_4', '2026-06-04 09:00:00', 'name_4'),
  (5, 'included_pattern_5', 'message_template_5', 5, 'description_5', 5, 'excluded_pattern_5', 'linkaged_id_5', '2026-06-05 09:00:00', 'name_5');

TRUNCATE TABLE "polestar"."cmm_alarm_pattern_noti_group";
INSERT INTO "polestar"."cmm_alarm_pattern_noti_group" ("pattern_noti_id", "group_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_pattern_noti_rmtype";
INSERT INTO "polestar"."cmm_alarm_pattern_noti_rmtype" ("pattern_noti_id", "manager_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_pattern_noti_user";
INSERT INTO "polestar"."cmm_alarm_pattern_noti_user" ("pattern_noti_id", "user_id") VALUES
  (1, 'user_id_1'),
  (2, 'user_id_2'),
  (3, 'user_id_3'),
  (4, 'user_id_4'),
  (5, 'user_id_5');

TRUNCATE TABLE "polestar"."cmm_alarm_pattern_type_target";
INSERT INTO "polestar"."cmm_alarm_pattern_type_target" ("pattern_noti_def_id", "resource_type") VALUES
  (1, 'resource_type_1'),
  (2, 'resource_type_2'),
  (3, 'resource_type_3'),
  (4, 'resource_type_4'),
  (5, 'resource_type_5');

TRUNCATE TABLE "polestar"."cmm_alarm_severity_display";
INSERT INTO "polestar"."cmm_alarm_severity_display" ("severity", "audiofile", "audiofilename", "audioupdatetime", "color", "displayname", "icon") VALUES
  (1, 1, 'audiofilename_1', 1, 1, 'displayname_1', 1),
  (2, 2, 'audiofilename_2', 2, 2, 'displayname_2', 2),
  (3, 3, 'audiofilename_3', 3, 3, 'displayname_3', 3),
  (4, 4, 'audiofilename_4', 4, 4, 'displayname_4', 4),
  (5, 5, 'audiofilename_5', 5, 5, 'displayname_5', 5);

TRUNCATE TABLE "polestar"."cmm_alarm_trg_action";
INSERT INTO "polestar"."cmm_alarm_trg_action" ("dtype", "id", "account", "actiondetail", "alarmseverity", "name", "scriptname", "severityoperator", "timeoutsec", "time_stamp", "definition_id") VALUES
  ('dtype_1', 1, 'account_1', 'actiondetail_1', 1, 'name_1', 'scriptname_1', 1, 1, 1, 1),
  ('dtype_2', 2, 'account_2', 'actiondetail_2', 2, 'name_2', 'scriptname_2', 2, 2, 2, 2),
  ('dtype_3', 3, 'account_3', 'actiondetail_3', 3, 'name_3', 'scriptname_3', 3, 3, 3, 3),
  ('dtype_4', 4, 'account_4', 'actiondetail_4', 4, 'name_4', 'scriptname_4', 4, 4, 4, 4),
  ('dtype_5', 5, 'account_5', 'actiondetail_5', 5, 'name_5', 'scriptname_5', 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_alarm_trg_action_log";
INSERT INTO "polestar"."cmm_alarm_trg_action_log" ("id", "actiondetail", "actionname", "actionstatus", "completiontime", "resultmessage", "starttime", "triggeractiontype", "alarm_id") VALUES
  (1, 'actiondetail_1', 'actionname_1', 'actionstatus_1', 1, 'resultmessage_1', 1, 'triggeractiontype_1', 1),
  (2, 'actiondetail_2', 'actionname_2', 'actionstatus_2', 2, 'resultmessage_2', 2, 'triggeractiontype_2', 2),
  (3, 'actiondetail_3', 'actionname_3', 'actionstatus_3', 3, 'resultmessage_3', 3, 'triggeractiontype_3', 3),
  (4, 'actiondetail_4', 'actionname_4', 'actionstatus_4', 4, 'resultmessage_4', 4, 'triggeractiontype_4', 4),
  (5, 'actiondetail_5', 'actionname_5', 'actionstatus_5', 5, 'resultmessage_5', 5, 'triggeractiontype_5', 5);

TRUNCATE TABLE "polestar"."cmm_availability";
INSERT INTO "polestar"."cmm_availability" ("avail_pk", "disable_rate", "down_rate", "time_stamp", "unknown_rate", "up_rate") VALUES
  ('avail_pk_1', 1.5, 1.5, 1, 1.5, 1.5),
  ('avail_pk_2', 2.5, 2.5, 2, 2.5, 2.5),
  ('avail_pk_3', 3.5, 3.5, 3, 3.5, 3.5),
  ('avail_pk_4', 4.5, 4.5, 4, 4.5, 4.5),
  ('avail_pk_5', 5.5, 5.5, 5, 5.5, 5.5);

TRUNCATE TABLE "polestar"."cmm_availability_log";
INSERT INTO "polestar"."cmm_availability_log" ("id", "status", "time_stamp", "resource_id") VALUES
  (1, 1, 1, 1),
  (2, 2, 2, 2),
  (3, 3, 3, 3),
  (4, 4, 4, 4),
  (5, 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_code";
INSERT INTO "polestar"."cmm_code" ("id", "code_name", "description", "message", "type") VALUES
  (1, 'code_name_1', 'description_1', 'message_1', 'type_1'),
  (2, 'code_name_2', 'description_2', 'message_2', 'type_2'),
  (3, 'code_name_3', 'description_3', 'message_3', 'type_3'),
  (4, 'code_name_4', 'description_4', 'message_4', 'type_4'),
  (5, 'code_name_5', 'description_5', 'message_5', 'type_5');

TRUNCATE TABLE "polestar"."cmm_conf_columns_info";
INSERT INTO "polestar"."cmm_conf_columns_info" ("user_id", "db2_columns", "mariadb_columns", "mssql_columns", "mysql_columns", "network_columns", "oracle_columns", "postgresql_columns", "saphana_columns", "server_columns", "sybase_columns", "tibero_columns", "was_columns", "icmp_columns") VALUES
  ('user_id_1', 'db2_columns_1', 'mariadb_columns_1', 'mssql_columns_1', 'mysql_columns_1', 'network_columns_1', 'oracle_columns_1', 'postgresql_columns_1', 'saphana_columns_1', 'server_columns_1', 'sybase_columns_1', 'tibero_columns_1', 'was_columns_1', 'icmp_columns_1'),
  ('user_id_2', 'db2_columns_2', 'mariadb_columns_2', 'mssql_columns_2', 'mysql_columns_2', 'network_columns_2', 'oracle_columns_2', 'postgresql_columns_2', 'saphana_columns_2', 'server_columns_2', 'sybase_columns_2', 'tibero_columns_2', 'was_columns_2', 'icmp_columns_2'),
  ('user_id_3', 'db2_columns_3', 'mariadb_columns_3', 'mssql_columns_3', 'mysql_columns_3', 'network_columns_3', 'oracle_columns_3', 'postgresql_columns_3', 'saphana_columns_3', 'server_columns_3', 'sybase_columns_3', 'tibero_columns_3', 'was_columns_3', 'icmp_columns_3'),
  ('user_id_4', 'db2_columns_4', 'mariadb_columns_4', 'mssql_columns_4', 'mysql_columns_4', 'network_columns_4', 'oracle_columns_4', 'postgresql_columns_4', 'saphana_columns_4', 'server_columns_4', 'sybase_columns_4', 'tibero_columns_4', 'was_columns_4', 'icmp_columns_4'),
  ('user_id_5', 'db2_columns_5', 'mariadb_columns_5', 'mssql_columns_5', 'mysql_columns_5', 'network_columns_5', 'oracle_columns_5', 'postgresql_columns_5', 'saphana_columns_5', 'server_columns_5', 'sybase_columns_5', 'tibero_columns_5', 'was_columns_5', 'icmp_columns_5');

TRUNCATE TABLE "polestar"."cmm_custom_cal_m_src";
INSERT INTO "polestar"."cmm_custom_cal_m_src" ("id", "calculator", "description", "expression_str", "name", "units", "uuid", "visibility", "custom_monitor_resource_type") VALUES
  (1, 'calculator_1', 'description_1', 'expression_str_1', 'name_1', 'units_1', 'uuid_1', 1, 'custom_monitor_resource_type_1'),
  (2, 'calculator_2', 'description_2', 'expression_str_2', 'name_2', 'units_2', 'uuid_2', 2, 'custom_monitor_resource_type_2'),
  (3, 'calculator_3', 'description_3', 'expression_str_3', 'name_3', 'units_3', 'uuid_3', 3, 'custom_monitor_resource_type_3'),
  (4, 'calculator_4', 'description_4', 'expression_str_4', 'name_4', 'units_4', 'uuid_4', 4, 'custom_monitor_resource_type_4'),
  (5, 'calculator_5', 'description_5', 'expression_str_5', 'name_5', 'units_5', 'uuid_5', 5, 'custom_monitor_resource_type_5');

TRUNCATE TABLE "polestar"."cmm_custom_conf_def";
INSERT INTO "polestar"."cmm_custom_conf_def" ("conf_def_id") VALUES
  (1),
  (2),
  (3),
  (4),
  (5);

TRUNCATE TABLE "polestar"."cmm_custom_conf_src";
INSERT INTO "polestar"."cmm_custom_conf_src" ("id", "description", "expression_str", "name", "uuid", "visibility", "custom_monitor_resource_type") VALUES
  (1, 'description_1', 'expression_str_1', 'name_1', 'uuid_1', 1, 'custom_monitor_resource_type_1'),
  (2, 'description_2', 'expression_str_2', 'name_2', 'uuid_2', 2, 'custom_monitor_resource_type_2'),
  (3, 'description_3', 'expression_str_3', 'name_3', 'uuid_3', 3, 'custom_monitor_resource_type_3'),
  (4, 'description_4', 'expression_str_4', 'name_4', 'uuid_4', 4, 'custom_monitor_resource_type_4'),
  (5, 'description_5', 'expression_str_5', 'name_5', 'uuid_5', 5, 'custom_monitor_resource_type_5');

TRUNCATE TABLE "polestar"."cmm_custom_mon";
INSERT INTO "polestar"."cmm_custom_mon" ("resourcetype", "description", "is_inner", "name", "version") VALUES
  ('resourcetype_1', 'description_1', 1, 'name_1', 1),
  ('resourcetype_2', 'description_2', 2, 'name_2', 2),
  ('resourcetype_3', 'description_3', 3, 'name_3', 3),
  ('resourcetype_4', 'description_4', 4, 'name_4', 4),
  ('resourcetype_5', 'description_5', 5, 'name_5', 5);

TRUNCATE TABLE "polestar"."cmm_custom_mon_m_src";
INSERT INTO "polestar"."cmm_custom_mon_m_src" ("id", "aggregatefuntion", "datatype", "description", "expression_str", "name", "numerictype", "savehistory", "scaletype", "scalevalue", "units", "uuid", "visibility", "custom_monitor_resource_type") VALUES
  (1, 'aggregatefuntion_1', 1, 'description_1', 'expression_str_1', 'name_1', 1, 1, 'scaletype_1', 1, 'units_1', 'uuid_1', 1, 'custom_monitor_resource_type_1'),
  (2, 'aggregatefuntion_2', 2, 'description_2', 'expression_str_2', 'name_2', 2, 2, 'scaletype_2', 2, 'units_2', 'uuid_2', 2, 'custom_monitor_resource_type_2'),
  (3, 'aggregatefuntion_3', 3, 'description_3', 'expression_str_3', 'name_3', 3, 3, 'scaletype_3', 3, 'units_3', 'uuid_3', 3, 'custom_monitor_resource_type_3'),
  (4, 'aggregatefuntion_4', 4, 'description_4', 'expression_str_4', 'name_4', 4, 4, 'scaletype_4', 4, 'units_4', 'uuid_4', 4, 'custom_monitor_resource_type_4'),
  (5, 'aggregatefuntion_5', 5, 'description_5', 'expression_str_5', 'name_5', 5, 5, 'scaletype_5', 5, 'units_5', 'uuid_5', 5, 'custom_monitor_resource_type_5');

TRUNCATE TABLE "polestar"."cmm_dependency_context";
INSERT INTO "polestar"."cmm_dependency_context" ("id", "conn_resource_id", "homo_key", "link_key", "link_type", "relation", "resource_id", "start_point", "upper") VALUES
  (1, 1, 'homo_key_1', 'link_key_1', 1, 1, 1, 1, 1),
  (2, 2, 'homo_key_2', 'link_key_2', 2, 2, 2, 2, 2),
  (3, 3, 'homo_key_3', 'link_key_3', 3, 3, 3, 3, 3),
  (4, 4, 'homo_key_4', 'link_key_4', 4, 4, 4, 4, 4),
  (5, 5, 'homo_key_5', 'link_key_5', 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_dependency_exclude";
INSERT INTO "polestar"."cmm_dependency_exclude" ("id", "ip_address", "name", "port") VALUES
  (1, 'ip_address_1', 'name_1', 1),
  (2, 'ip_address_2', 'name_2', 2),
  (3, 'ip_address_3', 'name_3', 3),
  (4, 'ip_address_4', 'name_4', 4),
  (5, 'ip_address_5', 'name_5', 5);

TRUNCATE TABLE "polestar"."cmm_dependency_link";
INSERT INTO "polestar"."cmm_dependency_link" ("id", "ctime", "direction", "discovery_type", "high_homo_key", "high_low_relation", "high_resource_id", "link_key", "link_name", "link_type", "low_homo_key", "low_resource_id", "mtime", "relation", "separation_key", "strong") VALUES
  (1, 1, 1, 1, 'high_homo_key_1', 1, 1, 'link_key_1', 'link_name_1', 1, 'low_homo_key_1', 1, 1, 1, 'separation_key_1', 1),
  (2, 2, 2, 2, 'high_homo_key_2', 2, 2, 'link_key_2', 'link_name_2', 2, 'low_homo_key_2', 2, 2, 2, 'separation_key_2', 2),
  (3, 3, 3, 3, 'high_homo_key_3', 3, 3, 'link_key_3', 'link_name_3', 3, 'low_homo_key_3', 3, 3, 3, 'separation_key_3', 3),
  (4, 4, 4, 4, 'high_homo_key_4', 4, 4, 'link_key_4', 'link_name_4', 4, 'low_homo_key_4', 4, 4, 4, 'separation_key_4', 4),
  (5, 5, 5, 5, 'high_homo_key_5', 5, 5, 'link_key_5', 'link_name_5', 5, 'low_homo_key_5', 5, 5, 5, 'separation_key_5', 5);

TRUNCATE TABLE "polestar"."cmm_device_priority";
INSERT INTO "polestar"."cmm_device_priority" ("id", "defaultpriority", "description", "name") VALUES
  (1, 1, 'description_1', 'name_1'),
  (2, 2, 'description_2', 'name_2'),
  (3, 3, 'description_3', 'name_3'),
  (4, 4, 'description_4', 'name_4'),
  (5, 5, 'description_5', 'name_5');

TRUNCATE TABLE "polestar"."cmm_device_priority_mm";
INSERT INTO "polestar"."cmm_device_priority_mm" ("id", "device_type", "managed", "name", "resource_type", "definition_id", "priority_id") VALUES
  (1, 'device_type_1', 1, 'name_1', 'resource_type_1', 1, 1),
  (2, 'device_type_2', 2, 'name_2', 'resource_type_2', 2, 2),
  (3, 'device_type_3', 3, 'name_3', 'resource_type_3', 3, 3),
  (4, 'device_type_4', 4, 'name_4', 'resource_type_4', 4, 4),
  (5, 'device_type_5', 5, 'name_5', 'resource_type_5', 5, 5);

TRUNCATE TABLE "polestar"."cmm_device_priority_period";
INSERT INTO "polestar"."cmm_device_priority_period" ("id", "device_type", "period", "priority_id", "resource_type") VALUES
  (1, 'device_type_1', 1, 1, 'resource_type_1'),
  (2, 'device_type_2', 2, 2, 'resource_type_2'),
  (3, 'device_type_3', 3, 3, 'resource_type_3'),
  (4, 'device_type_4', 4, 4, 'resource_type_4'),
  (5, 'device_type_5', 5, 5, 'resource_type_5');

TRUNCATE TABLE "polestar"."cmm_download_file_info";
INSERT INTO "polestar"."cmm_download_file_info" ("id", "description", "update_file", "file_name", "file_size", "manager_id", "upload_time") VALUES
  (1, 'description_1', 1, 'file_name_1', 1, 'manager_id_1', '2026-06-01 09:00:00'),
  (2, 'description_2', 2, 'file_name_2', 2, 'manager_id_2', '2026-06-02 09:00:00'),
  (3, 'description_3', 3, 'file_name_3', 3, 'manager_id_3', '2026-06-03 09:00:00'),
  (4, 'description_4', 4, 'file_name_4', 4, 'manager_id_4', '2026-06-04 09:00:00'),
  (5, 'description_5', 5, 'file_name_5', 5, 'manager_id_5', '2026-06-05 09:00:00');

TRUNCATE TABLE "polestar"."cmm_event_console_filter";
INSERT INTO "polestar"."cmm_event_console_filter" ("user_id") VALUES
  ('user_id_1'),
  ('user_id_2'),
  ('user_id_3'),
  ('user_id_4'),
  ('user_id_5');

TRUNCATE TABLE "polestar"."cmm_event_console_filter_col";
INSERT INTO "polestar"."cmm_event_console_filter_col" ("user_id", "column_name") VALUES
  ('user_id_1', 1),
  ('user_id_2', 2),
  ('user_id_3', 3),
  ('user_id_4', 4),
  ('user_id_5', 5);

TRUNCATE TABLE "polestar"."cmm_event_def";
INSERT INTO "polestar"."cmm_event_def" ("id", "description", "displayname", "name", "resource_type") VALUES
  (1, 'description_1', 'displayname_1', 'name_1', 'resource_type_1'),
  (2, 'description_2', 'displayname_2', 'name_2', 'resource_type_2'),
  (3, 'description_3', 'displayname_3', 'name_3', 'resource_type_3'),
  (4, 'description_4', 'displayname_4', 'name_4', 'resource_type_4'),
  (5, 'description_5', 'displayname_5', 'name_5', 'resource_type_5');

TRUNCATE TABLE "polestar"."cmm_event_source";
INSERT INTO "polestar"."cmm_event_source" ("id", "issystem", "location", "definition_id", "resource_id") VALUES
  (1, 1, 'location_1', 1, 1),
  (2, 2, 'location_2', 2, 2),
  (3, 3, 'location_3', 3, 3),
  (4, 4, 'location_4', 4, 4),
  (5, 5, 'location_5', 5, 5);

TRUNCATE TABLE "polestar"."cmm_expect";
INSERT INTO "polestar"."cmm_expect" ("id", "expect_str", "order_num", "is_output", "is_regx", "send_command", "expectscript_name") VALUES
  ('id_1', 'expect_str_1', 1, 1, 1, 'send_command_1', 'expectscript_name_1'),
  ('id_2', 'expect_str_2', 2, 2, 2, 'send_command_2', 'expectscript_name_2'),
  ('id_3', 'expect_str_3', 3, 3, 3, 'send_command_3', 'expectscript_name_3'),
  ('id_4', 'expect_str_4', 4, 4, 4, 'send_command_4', 'expectscript_name_4'),
  ('id_5', 'expect_str_5', 5, 5, 5, 'send_command_5', 'expectscript_name_5');

TRUNCATE TABLE "polestar"."cmm_expect_script";
INSERT INTO "polestar"."cmm_expect_script" ("name", "description", "is_telnet_auto_login", "optlock", "timeout", "type") VALUES
  ('name_1', 'description_1', 1, 1, 1, 'type_1'),
  ('name_2', 'description_2', 2, 2, 2, 'type_2'),
  ('name_3', 'description_3', 3, 3, 3, 'type_3'),
  ('name_4', 'description_4', 4, 4, 4, 'type_4'),
  ('name_5', 'description_5', 5, 5, 5, 'type_5');

TRUNCATE TABLE "polestar"."cmm_expect_script_job";
INSERT INTO "polestar"."cmm_expect_script_job" ("id", "ctime", "description", "expect_script_name", "mtime", "modifiedby", "name", "optlock", "ostype", "is_running", "starttime", "vendor") VALUES
  (1, 1, 'description_1', 'expect_script_name_1', 1, 'modifiedby_1', 'name_1', 1, 'ostype_1', 1, 1, 'vendor_1'),
  (2, 2, 'description_2', 'expect_script_name_2', 2, 'modifiedby_2', 'name_2', 2, 'ostype_2', 2, 2, 'vendor_2'),
  (3, 3, 'description_3', 'expect_script_name_3', 3, 'modifiedby_3', 'name_3', 3, 'ostype_3', 3, 3, 'vendor_3'),
  (4, 4, 'description_4', 'expect_script_name_4', 4, 'modifiedby_4', 'name_4', 4, 'ostype_4', 4, 4, 'vendor_4'),
  (5, 5, 'description_5', 'expect_script_name_5', 5, 'modifiedby_5', 'name_5', 5, 'ostype_5', 5, 5, 'vendor_5');

TRUNCATE TABLE "polestar"."cmm_expect_script_job_resource";
INSERT INTO "polestar"."cmm_expect_script_job_resource" ("job_id", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_expect_script_result";
INSERT INTO "polestar"."cmm_expect_script_result" ("id", "ctime", "expect_script_name", "is_changed", "is_success", "result_message", "expect_script_job_id", "resource_id", "occurrencejob") VALUES
  (1, 1, 'expect_script_name_1', 1, 1, 'result_message_1', 1, 1, 'occurrencejob_1'),
  (2, 2, 'expect_script_name_2', 2, 2, 'result_message_2', 2, 2, 'occurrencejob_2'),
  (3, 3, 'expect_script_name_3', 3, 3, 'result_message_3', 3, 3, 'occurrencejob_3'),
  (4, 4, 'expect_script_name_4', 4, 4, 'result_message_4', 4, 4, 'occurrencejob_4'),
  (5, 5, 'expect_script_name_5', 5, 5, 'result_message_5', 5, 5, 'occurrencejob_5');

TRUNCATE TABLE "polestar"."cmm_expect_script_trigger";
INSERT INTO "polestar"."cmm_expect_script_trigger" ("id", "day_val", "day_of_week_val", "expressionsummary", "hour_val", "intervaltype", "min_val", "month_val", "starttime", "time_stamp", "job_id") VALUES
  (1, 1, 1, 'expressionsummary_1', 1, 1, 1, 1, '2026-06-01 09:00:00', 1, 1),
  (2, 2, 2, 'expressionsummary_2', 2, 2, 2, 2, '2026-06-02 09:00:00', 2, 2),
  (3, 3, 3, 'expressionsummary_3', 3, 3, 3, 3, '2026-06-03 09:00:00', 3, 3),
  (4, 4, 4, 'expressionsummary_4', 4, 4, 4, 4, '2026-06-04 09:00:00', 4, 4),
  (5, 5, 5, 'expressionsummary_5', 5, 5, 5, 5, '2026-06-05 09:00:00', 5, 5);

TRUNCATE TABLE "polestar"."cmm_importance";
INSERT INTO "polestar"."cmm_importance" ("id", "cpuinterval", "defaultgrade", "description", "diskinterval", "grade", "memoryinterval", "networkinterval", "processinterval") VALUES
  (1, 1, 1, 'description_1', 1, 'grade_1', 1, 1, 1),
  (2, 2, 2, 'description_2', 2, 'grade_2', 2, 2, 2),
  (3, 3, 3, 'description_3', 3, 'grade_3', 3, 3, 3),
  (4, 4, 4, 'description_4', 4, 'grade_4', 4, 4, 4),
  (5, 5, 5, 'description_5', 5, 'grade_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_job_history";
INSERT INTO "polestar"."cmm_job_history" ("dtype", "id", "ctime", "completetime", "mtime", "message", "name", "starttime", "status_type", "target_count", "target_names", "user_id", "user_name", "excluded", "job_id", "jobtype", "pattern") VALUES
  ('dtype_1', 1, 1, 1, 1, 'message_1', 'name_1', 1, 'status_type_1', 1, 'target_names_1', 'user_id_1', 'user_name_1', 1, 1, 1, 'pattern_1'),
  ('dtype_2', 2, 2, 2, 2, 'message_2', 'name_2', 2, 'status_type_2', 2, 'target_names_2', 'user_id_2', 'user_name_2', 2, 2, 2, 'pattern_2'),
  ('dtype_3', 3, 3, 3, 3, 'message_3', 'name_3', 3, 'status_type_3', 3, 'target_names_3', 'user_id_3', 'user_name_3', 3, 3, 3, 'pattern_3'),
  ('dtype_4', 4, 4, 4, 4, 'message_4', 'name_4', 4, 'status_type_4', 4, 'target_names_4', 'user_id_4', 'user_name_4', 4, 4, 4, 'pattern_4'),
  ('dtype_5', 5, 5, 5, 5, 'message_5', 'name_5', 5, 'status_type_5', 5, 'target_names_5', 'user_id_5', 'user_name_5', 5, 5, 5, 'pattern_5');

TRUNCATE TABLE "polestar"."cmm_job_history_ids";
INSERT INTO "polestar"."cmm_job_history_ids" ("jobhistory_id", "resource_ids") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_job_history_platform";
INSERT INTO "polestar"."cmm_job_history_platform" ("jobhistory_id", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_maintenance_job";
INSERT INTO "polestar"."cmm_maintenance_job" ("id", "ctime", "completejobautorecovery", "completetime", "description", "excluded", "jobtype", "mtime", "modifiedby", "name", "nowstart", "nowstarttimemin", "optlock", "pattern", "is_running", "starttime", "maintenanceremainnotitime", "maintenancestatusnoti", "notitarget") VALUES
  (1, 1, 1, 1, 'description_1', 1, 'jobtype_1', 1, 'modifiedby_1', 'name_1', 1, 1, 1, 'pattern_1', 1, 1, 1, 1, 'notitarget_1'),
  (2, 2, 2, 2, 'description_2', 2, 'jobtype_2', 2, 'modifiedby_2', 'name_2', 2, 2, 2, 'pattern_2', 2, 2, 2, 2, 'notitarget_2'),
  (3, 3, 3, 3, 'description_3', 3, 'jobtype_3', 3, 'modifiedby_3', 'name_3', 3, 3, 3, 'pattern_3', 3, 3, 3, 3, 'notitarget_3'),
  (4, 4, 4, 4, 'description_4', 4, 'jobtype_4', 4, 'modifiedby_4', 'name_4', 4, 4, 4, 'pattern_4', 4, 4, 4, 4, 'notitarget_4'),
  (5, 5, 5, 5, 'description_5', 5, 'jobtype_5', 5, 'modifiedby_5', 'name_5', 5, 5, 5, 'pattern_5', 5, 5, 5, 5, 'notitarget_5');

TRUNCATE TABLE "polestar"."cmm_maintenance_job_bizskd";
INSERT INTO "polestar"."cmm_maintenance_job_bizskd" ("job_id", "schedule_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_maintenance_job_resource";
INSERT INTO "polestar"."cmm_maintenance_job_resource" ("job_id", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_maintenance_noti_target_user";
INSERT INTO "polestar"."cmm_maintenance_noti_target_user" ("maintenance_job_id", "targetusers", "targetresourcemanager") VALUES
  (1, 'targetusers_1', 1),
  (2, 'targetusers_2', 2),
  (3, 'targetusers_3', 3),
  (4, 'targetusers_4', 4),
  (5, 'targetusers_5', 5);

TRUNCATE TABLE "polestar"."cmm_maintenance_noti_user";
INSERT INTO "polestar"."cmm_maintenance_noti_user" ("maintenance_job_id", "targetusers", "targetresourcemanager") VALUES
  (1, 'targetusers_1', 1),
  (2, 'targetusers_2', 2),
  (3, 'targetusers_3', 3),
  (4, 'targetusers_4', 4),
  (5, 'targetusers_5', 5);

TRUNCATE TABLE "polestar"."cmm_maintenance_trigger";
INSERT INTO "polestar"."cmm_maintenance_trigger" ("id", "is_complete_delete", "day_val", "day_of_week_val", "expressionsummary", "hour_val", "intervalfixtype", "intervaltype", "min_val", "month_val", "starttime", "timemin", "time_stamp", "week_val", "job_id") VALUES
  (1, 1, 1, 1, 'expressionsummary_1', 1, 1, 1, 1, 1, '2026-06-01 09:00:00', 1, 1, 1, 1),
  (2, 2, 2, 2, 'expressionsummary_2', 2, 2, 2, 2, 2, '2026-06-02 09:00:00', 2, 2, 2, 2),
  (3, 3, 3, 3, 'expressionsummary_3', 3, 3, 3, 3, 3, '2026-06-03 09:00:00', 3, 3, 3, 3),
  (4, 4, 4, 4, 'expressionsummary_4', 4, 4, 4, 4, 4, '2026-06-04 09:00:00', 4, 4, 4, 4),
  (5, 5, 5, 5, 'expressionsummary_5', 5, 5, 5, 5, 5, '2026-06-05 09:00:00', 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_measurement";
INSERT INTO "polestar"."cmm_measurement" ("dtype", "resource_id", "definition_id", "error_message", "lastupdatedtime", "name", "time_stamp", "availabilitystatus", "numeric_value", "raw_value", "starttime", "string_value") VALUES
  ('dtype_1', 1, 1, 'error_message_1', 1, 'name_1', 1, 'availabilitystatus_1', 1.5, 1.5, 1, 'string_value_1'),
  ('dtype_2', 2, 2, 'error_message_2', 2, 'name_2', 2, 'availabilitystatus_2', 2.5, 2.5, 2, 'string_value_2'),
  ('dtype_3', 3, 3, 'error_message_3', 3, 'name_3', 3, 'availabilitystatus_3', 3.5, 3.5, 3, 'string_value_3'),
  ('dtype_4', 4, 4, 'error_message_4', 4, 'name_4', 4, 'availabilitystatus_4', 4.5, 4.5, 4, 'string_value_4'),
  ('dtype_5', 5, 5, 'error_message_5', 5, 'name_5', 5, 'availabilitystatus_5', 5.5, 5.5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_measurement_def";
INSERT INTO "polestar"."cmm_measurement_def" ("id", "is_analytics_target", "is_deleted", "description", "displayname", "displaytype", "measurementtype", "metrictype", "name", "numerictype", "priority", "protocolinfo", "resource_type", "save_history", "is_measurement_target", "tabulardataclass", "units", "visibility") VALUES
  (1, 1, 1, 'description_1', 'displayname_1', 1, 1, 1, 'name_1', 1, 1, 'protocolinfo_1', 'resource_type_1', 1, 1, 'tabulardataclass_1', 'units_1', 1),
  (2, 2, 2, 'description_2', 'displayname_2', 2, 2, 2, 'name_2', 2, 2, 'protocolinfo_2', 'resource_type_2', 2, 2, 'tabulardataclass_2', 'units_2', 2),
  (3, 3, 3, 'description_3', 'displayname_3', 3, 3, 3, 'name_3', 3, 3, 'protocolinfo_3', 'resource_type_3', 3, 3, 'tabulardataclass_3', 'units_3', 3),
  (4, 4, 4, 'description_4', 'displayname_4', 4, 4, 4, 'name_4', 4, 4, 'protocolinfo_4', 'resource_type_4', 4, 4, 'tabulardataclass_4', 'units_4', 4),
  (5, 5, 5, 'description_5', 'displayname_5', 5, 5, 5, 'name_5', 5, 5, 'protocolinfo_5', 'resource_type_5', 5, 5, 'tabulardataclass_5', 'units_5', 5);

TRUNCATE TABLE "polestar"."cmm_measurement_def_tabular";
INSERT INTO "polestar"."cmm_measurement_def_tabular" ("id", "attributetype", "is_deleted", "description", "displayname", "esfieldname", "name", "summary", "units", "visibility", "measurement_def_id") VALUES
  (1, 'attributetype_1', 1, 'description_1', 'displayname_1', 'esfieldname_1', 'name_1', 1, 'units_1', 1, 1),
  (2, 'attributetype_2', 2, 'description_2', 'displayname_2', 'esfieldname_2', 'name_2', 2, 'units_2', 2, 2),
  (3, 'attributetype_3', 3, 'description_3', 'displayname_3', 'esfieldname_3', 'name_3', 3, 'units_3', 3, 3),
  (4, 'attributetype_4', 4, 'description_4', 'displayname_4', 'esfieldname_4', 'name_4', 4, 'units_4', 4, 4),
  (5, 'attributetype_5', 5, 'description_5', 'displayname_5', 'esfieldname_5', 'name_5', 5, 'units_5', 5, 5);

TRUNCATE TABLE "polestar"."cmm_message_template";
INSERT INTO "polestar"."cmm_message_template" ("id", "description", "message", "priority", "type", "type_spec") VALUES
  (1, 'description_1', 'message_1', 1, 'type_1', 'type_spec_1'),
  (2, 'description_2', 'message_2', 2, 'type_2', 'type_spec_2'),
  (3, 'description_3', 'message_3', 3, 'type_3', 'type_spec_3'),
  (4, 'description_4', 'message_4', 4, 'type_4', 'type_spec_4'),
  (5, 'description_5', 'message_5', 5, 'type_5', 'type_spec_5');

TRUNCATE TABLE "polestar"."cmm_metric_collection";
INSERT INTO "polestar"."cmm_metric_collection" ("id", "cloudtype", "collected", "name", "resource_type", "definition_id", "metric_management_id") VALUES
  (1, 'cloudtype_1', 1, 'name_1', 'resource_type_1', 1, 1),
  (2, 'cloudtype_2', 2, 'name_2', 'resource_type_2', 2, 2),
  (3, 'cloudtype_3', 3, 'name_3', 'resource_type_3', 3, 3),
  (4, 'cloudtype_4', 4, 'name_4', 'resource_type_4', 4, 4),
  (5, 'cloudtype_5', 5, 'name_5', 'resource_type_5', 5, 5);

TRUNCATE TABLE "polestar"."cmm_metric_management";
INSERT INTO "polestar"."cmm_metric_management" ("id", "ctime", "defaultmetricmanagement", "description", "name", "resource_set_auto_priority", "resource_set_auto_used") VALUES
  (1, 1, 1, 'description_1', 'name_1', 1, 'resource_set_auto_used_1'),
  (2, 2, 2, 'description_2', 'name_2', 2, 'resource_set_auto_used_2'),
  (3, 3, 3, 'description_3', 'name_3', 3, 'resource_set_auto_used_3'),
  (4, 4, 4, 'description_4', 'name_4', 4, 'resource_set_auto_used_4'),
  (5, 5, 5, 'description_5', 'name_5', 5, 'resource_set_auto_used_5');

TRUNCATE TABLE "polestar"."cmm_metric_management_con";
INSERT INTO "polestar"."cmm_metric_management_con" ("dtype", "id", "conjunction", "order_number", "time_stamp", "matching_value", "matching_value2", "value_string", "value_string2", "metric_management_id") VALUES
  ('dtype_1', 'id_1', 'conjunction_1', 1, 1, 'matching_value_1', 'matching_value2_1', 'value_string_1', 'value_string2_1', 1),
  ('dtype_2', 'id_2', 'conjunction_2', 2, 2, 'matching_value_2', 'matching_value2_2', 'value_string_2', 'value_string2_2', 2),
  ('dtype_3', 'id_3', 'conjunction_3', 3, 3, 'matching_value_3', 'matching_value2_3', 'value_string_3', 'value_string2_3', 3),
  ('dtype_4', 'id_4', 'conjunction_4', 4, 4, 'matching_value_4', 'matching_value2_4', 'value_string_4', 'value_string2_4', 4),
  ('dtype_5', 'id_5', 'conjunction_5', 5, 5, 'matching_value_5', 'matching_value2_5', 'value_string_5', 'value_string2_5', 5);

TRUNCATE TABLE "polestar"."cmm_metric_management_hist";
INSERT INTO "polestar"."cmm_metric_management_hist" ("id", "metric_management_id", "metric_management_mtime", "resource_id") VALUES
  (1, 1, 1, 1),
  (2, 2, 2, 2),
  (3, 3, 3, 3),
  (4, 4, 4, 4),
  (5, 5, 5, 5);

TRUNCATE TABLE "polestar"."cmm_metric_stat_d";
INSERT INTO "polestar"."cmm_metric_stat_d" ("resource_id", "definition_name", "stat_date", "avg_val", "bottom_val", "day_of_week", "hour_of_day", "max_val", "min_val", "top_val") VALUES
  (1, 'definition_name_1', 'stat_date_1', 1.5, 1.5, 1, 1, 1.5, 1.5, 1.5),
  (2, 'definition_name_2', 'stat_date_2', 2.5, 2.5, 2, 2, 2.5, 2.5, 2.5),
  (3, 'definition_name_3', 'stat_date_3', 3.5, 3.5, 3, 3, 3.5, 3.5, 3.5),
  (4, 'definition_name_4', 'stat_date_4', 4.5, 4.5, 4, 4, 4.5, 4.5, 4.5),
  (5, 'definition_name_5', 'stat_date_5', 5.5, 5.5, 5, 5, 5.5, 5.5, 5.5);

TRUNCATE TABLE "polestar"."cmm_metric_stat_d_t";
INSERT INTO "polestar"."cmm_metric_stat_d_t" ("resource_id", "definition_name", "stat_date", "avg_val", "bottom_val", "day_of_week", "hour_of_day", "max_val", "min_val", "top_val") VALUES
  (1, 'definition_name_1', 'stat_date_1', 1.5, 1.5, 1, 1, 1.5, 1.5, 1.5),
  (2, 'definition_name_2', 'stat_date_2', 2.5, 2.5, 2, 2, 2.5, 2.5, 2.5),
  (3, 'definition_name_3', 'stat_date_3', 3.5, 3.5, 3, 3, 3.5, 3.5, 3.5),
  (4, 'definition_name_4', 'stat_date_4', 4.5, 4.5, 4, 4, 4.5, 4.5, 4.5),
  (5, 'definition_name_5', 'stat_date_5', 5.5, 5.5, 5, 5, 5.5, 5.5, 5.5);

TRUNCATE TABLE "polestar"."cmm_metric_stat_h";
INSERT INTO "polestar"."cmm_metric_stat_h" ("resource_id", "definition_name", "stat_date", "avg_val", "bottom_val", "day_of_week", "hour_of_day", "max_val", "min_val", "top_val") VALUES
  (1, 'definition_name_1', 'stat_date_1', 1.5, 1.5, 1, 1, 1.5, 1.5, 1.5),
  (2, 'definition_name_2', 'stat_date_2', 2.5, 2.5, 2, 2, 2.5, 2.5, 2.5),
  (3, 'definition_name_3', 'stat_date_3', 3.5, 3.5, 3, 3, 3.5, 3.5, 3.5),
  (4, 'definition_name_4', 'stat_date_4', 4.5, 4.5, 4, 4, 4.5, 4.5, 4.5),
  (5, 'definition_name_5', 'stat_date_5', 5.5, 5.5, 5, 5, 5.5, 5.5, 5.5);

TRUNCATE TABLE "polestar"."cmm_metric_stat_h_t";
INSERT INTO "polestar"."cmm_metric_stat_h_t" ("resource_id", "definition_name", "stat_date", "avg_val", "bottom_val", "day_of_week", "hour_of_day", "max_val", "min_val", "top_val") VALUES
  (1, 'definition_name_1', 'stat_date_1', 1.5, 1.5, 1, 1, 1.5, 1.5, 1.5),
  (2, 'definition_name_2', 'stat_date_2', 2.5, 2.5, 2, 2, 2.5, 2.5, 2.5),
  (3, 'definition_name_3', 'stat_date_3', 3.5, 3.5, 3, 3, 3.5, 3.5, 3.5),
  (4, 'definition_name_4', 'stat_date_4', 4.5, 4.5, 4, 4, 4.5, 4.5, 4.5),
  (5, 'definition_name_5', 'stat_date_5', 5.5, 5.5, 5, 5, 5.5, 5.5, 5.5);

TRUNCATE TABLE "polestar"."cmm_metric_stat_m";
INSERT INTO "polestar"."cmm_metric_stat_m" ("resource_id", "definition_name", "stat_date", "avg_val", "bottom_val", "day_of_week", "hour_of_day", "max_val", "min_val", "top_val") VALUES
  (1, 'definition_name_1', 'stat_date_1', 1.5, 1.5, 1, 1, 1.5, 1.5, 1.5),
  (2, 'definition_name_2', 'stat_date_2', 2.5, 2.5, 2, 2, 2.5, 2.5, 2.5),
  (3, 'definition_name_3', 'stat_date_3', 3.5, 3.5, 3, 3, 3.5, 3.5, 3.5),
  (4, 'definition_name_4', 'stat_date_4', 4.5, 4.5, 4, 4, 4.5, 4.5, 4.5),
  (5, 'definition_name_5', 'stat_date_5', 5.5, 5.5, 5, 5, 5.5, 5.5, 5.5);

TRUNCATE TABLE "polestar"."cmm_metric_stat_m_t";
INSERT INTO "polestar"."cmm_metric_stat_m_t" ("resource_id", "definition_name", "stat_date", "avg_val", "bottom_val", "day_of_week", "hour_of_day", "max_val", "min_val", "top_val") VALUES
  (1, 'definition_name_1', 'stat_date_1', 1.5, 1.5, 1, 1, 1.5, 1.5, 1.5),
  (2, 'definition_name_2', 'stat_date_2', 2.5, 2.5, 2, 2, 2.5, 2.5, 2.5),
  (3, 'definition_name_3', 'stat_date_3', 3.5, 3.5, 3, 3, 3.5, 3.5, 3.5),
  (4, 'definition_name_4', 'stat_date_4', 4.5, 4.5, 4, 4, 4.5, 4.5, 4.5),
  (5, 'definition_name_5', 'stat_date_5', 5.5, 5.5, 5, 5, 5.5, 5.5, 5.5);

TRUNCATE TABLE "polestar"."cmm_metric_stat_table_info";
INSERT INTO "polestar"."cmm_metric_stat_table_info" ("table_name", "create_date", "elapsed_time", "row_count", "stat_type", "table_type", "update_date") VALUES
  ('table_name_1', 'create_date_1', 1, 1, 'stat_type_1', 'table_type_1', 'update_date_1'),
  ('table_name_2', 'create_date_2', 2, 2, 'stat_type_2', 'table_type_2', 'update_date_2'),
  ('table_name_3', 'create_date_3', 3, 3, 'stat_type_3', 'table_type_3', 'update_date_3'),
  ('table_name_4', 'create_date_4', 4, 4, 'stat_type_4', 'table_type_4', 'update_date_4'),
  ('table_name_5', 'create_date_5', 5, 5, 'stat_type_5', 'table_type_5', 'update_date_5');

TRUNCATE TABLE "polestar"."cmm_monitor_template";
INSERT INTO "polestar"."cmm_monitor_template" ("id", "acl_id", "ctime", "is_deleted", "description", "discoveryinterval", "mtime", "modifiedby", "name", "optlock", "ostype", "pql", "type", "uuid") VALUES
  ('id_1', 1, 1, 1, 'description_1', 1, 1, 'modifiedby_1', 'name_1', 1, 'ostype_1', 'pql_1', 'type_1', 'uuid_1'),
  ('id_2', 2, 2, 2, 'description_2', 2, 2, 'modifiedby_2', 'name_2', 2, 'ostype_2', 'pql_2', 'type_2', 'uuid_2'),
  ('id_3', 3, 3, 3, 'description_3', 3, 3, 'modifiedby_3', 'name_3', 3, 'ostype_3', 'pql_3', 'type_3', 'uuid_3'),
  ('id_4', 4, 4, 4, 'description_4', 4, 4, 'modifiedby_4', 'name_4', 4, 'ostype_4', 'pql_4', 'type_4', 'uuid_4'),
  ('id_5', 5, 5, 5, 'description_5', 5, 5, 'modifiedby_5', 'name_5', 5, 'ostype_5', 'pql_5', 'type_5', 'uuid_5');

TRUNCATE TABLE "polestar"."cmm_monitor_template_exception";
INSERT INTO "polestar"."cmm_monitor_template_exception" ("template_id", "resource_id") VALUES
  ('template_id_1', 1),
  ('template_id_2', 2),
  ('template_id_3', 3),
  ('template_id_4', 4),
  ('template_id_5', 5);

TRUNCATE TABLE "polestar"."cmm_monitor_template_target";
INSERT INTO "polestar"."cmm_monitor_template_target" ("template_id", "resource_id") VALUES
  ('template_id_1', 1),
  ('template_id_2', 2),
  ('template_id_3', 3),
  ('template_id_4', 4),
  ('template_id_5', 5);

TRUNCATE TABLE "polestar"."cmm_operation_def";
INSERT INTO "polestar"."cmm_operation_def" ("id", "add_alert", "defaulttimeout", "description", "displayname", "name", "resource_type", "param_conf_def_id", "result_conf_def_id") VALUES
  (1, 1, 1, 'description_1', 'displayname_1', 'name_1', 'resource_type_1', 1, 1),
  (2, 2, 2, 'description_2', 'displayname_2', 'name_2', 'resource_type_2', 2, 2),
  (3, 3, 3, 'description_3', 'displayname_3', 'name_3', 'resource_type_3', 3, 3),
  (4, 4, 4, 'description_4', 'displayname_4', 'name_4', 'resource_type_4', 4, 4),
  (5, 5, 5, 'description_5', 'displayname_5', 'name_5', 'resource_type_5', 5, 5);

TRUNCATE TABLE "polestar"."cmm_property_def_select";
INSERT INTO "polestar"."cmm_property_def_select" ("property_def_id", "selectlist") VALUES
  (1, 'selectlist_1'),
  (2, 'selectlist_2'),
  (3, 'selectlist_3'),
  (4, 'selectlist_4'),
  (5, 'selectlist_5');

TRUNCATE TABLE "polestar"."cmm_realtime_info";
INSERT INTO "polestar"."cmm_realtime_info" ("id", "expirationtime", "expired", "intervalms", "realtimestatus") VALUES
  (1, 1, 1, 1, 'realtimestatus_1'),
  (2, 2, 2, 2, 'realtimestatus_2'),
  (3, 3, 3, 3, 'realtimestatus_3'),
  (4, 4, 4, 4, 'realtimestatus_4'),
  (5, 5, 5, 5, 'realtimestatus_5');

TRUNCATE TABLE "polestar"."cmm_resource_lifecycle_history";
INSERT INTO "polestar"."cmm_resource_lifecycle_history" ("id", "description", "event_time", "lifecycle_type", "resource_type", "resource_id") VALUES
  (1, 'description_1', 1, 'lifecycle_type_1', 'resource_type_1', 1),
  (2, 'description_2', 2, 'lifecycle_type_2', 'resource_type_2', 2),
  (3, 'description_3', 3, 'lifecycle_type_3', 'resource_type_3', 3),
  (4, 'description_4', 4, 'lifecycle_type_4', 'resource_type_4', 4),
  (5, 'description_5', 5, 'lifecycle_type_5', 'resource_type_5', 5);

TRUNCATE TABLE "polestar"."cmm_resource_maintenance";
INSERT INTO "polestar"."cmm_resource_maintenance" ("resource_id", "is_maintenance") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_resource_path";
INSERT INTO "polestar"."cmm_resource_path" ("id", "groupidancestry", "grouppathname") VALUES
  (1, 'groupidancestry_1', 'grouppathname_1'),
  (2, 'groupidancestry_2', 'grouppathname_2'),
  (3, 'groupidancestry_3', 'grouppathname_3'),
  (4, 'groupidancestry_4', 'grouppathname_4'),
  (5, 'groupidancestry_5', 'grouppathname_5');

TRUNCATE TABLE "polestar"."cmm_resource_schedule";
INSERT INTO "polestar"."cmm_resource_schedule" ("resource_id", "mtime", "manager_id", "owner_manager_id", "scheduler_state", "seed_key") VALUES
  (1, 1, 'manager_id_1', 'owner_manager_id_1', 'scheduler_state_1', 1),
  (2, 2, 'manager_id_2', 'owner_manager_id_2', 'scheduler_state_2', 2),
  (3, 3, 'manager_id_3', 'owner_manager_id_3', 'scheduler_state_3', 3),
  (4, 4, 'manager_id_4', 'owner_manager_id_4', 'scheduler_state_4', 4),
  (5, 5, 'manager_id_5', 'owner_manager_id_5', 'scheduler_state_5', 5);

TRUNCATE TABLE "polestar"."cmm_resource_system";
INSERT INTO "polestar"."cmm_resource_system" ("id", "hostname", "inheritstatus", "ipaddress", "systemname") VALUES
  (1, 'hostname_1', 1, 'ipaddress_1', 'systemname_1'),
  (2, 'hostname_2', 2, 'ipaddress_2', 'systemname_2'),
  (3, 'hostname_3', 3, 'ipaddress_3', 'systemname_3'),
  (4, 'hostname_4', 4, 'ipaddress_4', 'systemname_4'),
  (5, 'hostname_5', 5, 'ipaddress_5', 'systemname_5');

TRUNCATE TABLE "polestar"."cmm_resource_type";
INSERT INTO "polestar"."cmm_resource_type" ("name", "category", "is_custom_monitor_type", "is_deleted", "description", "is_disabled", "displayname", "managementpolicy", "measurementpollinginterval", "pollingpolicy", "resourceicon", "typename", "version", "conn_conf_def_id", "resource_conf_def_id") VALUES
  ('name_1', 1, 1, 1, 'description_1', 1, 'displayname_1', 1, 1, 1, 'resourceicon_1', 'typename_1', 'version_1', 1, 1),
  ('name_2', 2, 2, 2, 'description_2', 2, 'displayname_2', 2, 2, 2, 'resourceicon_2', 'typename_2', 'version_2', 2, 2),
  ('name_3', 3, 3, 3, 'description_3', 3, 'displayname_3', 3, 3, 3, 'resourceicon_3', 'typename_3', 'version_3', 3, 3),
  ('name_4', 4, 4, 4, 'description_4', 4, 'displayname_4', 4, 4, 4, 'resourceicon_4', 'typename_4', 'version_4', 4, 4),
  ('name_5', 5, 5, 5, 'description_5', 5, 'displayname_5', 5, 5, 5, 'resourceicon_5', 'typename_5', 'version_5', 5, 5);

TRUNCATE TABLE "polestar"."cmm_resource_type_parent";
INSERT INTO "polestar"."cmm_resource_type_parent" ("resourcetype_name", "parent_resource_type") VALUES
  ('resourcetype_name_1', 'parent_resource_type_1'),
  ('resourcetype_name_2', 'parent_resource_type_2'),
  ('resourcetype_name_3', 'parent_resource_type_3'),
  ('resourcetype_name_4', 'parent_resource_type_4'),
  ('resourcetype_name_5', 'parent_resource_type_5');

TRUNCATE TABLE "polestar"."cmm_resourcestatus_config_def";
INSERT INTO "polestar"."cmm_resourcestatus_config_def" ("id", "default_setting", "name", "priority", "resource_type", "search_option") VALUES
  (1, 1, 'name_1', 1, 'resource_type_1', 1),
  (2, 2, 'name_2', 2, 'resource_type_2', 2),
  (3, 3, 'name_3', 3, 'resource_type_3', 3),
  (4, 4, 'name_4', 4, 'resource_type_4', 4),
  (5, 5, 'name_5', 5, 'resource_type_5', 5);

TRUNCATE TABLE "polestar"."cmm_script_exception";
INSERT INTO "polestar"."cmm_script_exception" ("job_id", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_script_model_list";
INSERT INTO "polestar"."cmm_script_model_list" ("expectscriptjob_id", "model_name") VALUES
  (1, 'model_name_1'),
  (2, 'model_name_2'),
  (3, 'model_name_3'),
  (4, 'model_name_4'),
  (5, 'model_name_5');

TRUNCATE TABLE "polestar"."cmm_search_con_detail";
INSERT INTO "polestar"."cmm_search_con_detail" ("dtype", "id", "is_exclude", "name", "stringvalue", "parent_list_id", "search_condition_id") VALUES
  ('dtype_1', 1, 1, 'name_1', 'stringvalue_1', 1, 1),
  ('dtype_2', 2, 2, 'name_2', 'stringvalue_2', 2, 2),
  ('dtype_3', 3, 3, 'name_3', 'stringvalue_3', 3, 3),
  ('dtype_4', 4, 4, 'name_4', 'stringvalue_4', 4, 4),
  ('dtype_5', 5, 5, 'name_5', 'stringvalue_5', 5, 5);

TRUNCATE TABLE "polestar"."cmm_search_condition";
INSERT INTO "polestar"."cmm_search_condition" ("id", "description", "is_favorite", "last_applied_date", "name", "type", "userid") VALUES
  (1, 'description_1', 1, 1, 'name_1', 'type_1', 'userid_1'),
  (2, 'description_2', 2, 2, 'name_2', 'type_2', 'userid_2'),
  (3, 'description_3', 3, 3, 'name_3', 'type_3', 'userid_3'),
  (4, 'description_4', 4, 4, 'name_4', 'type_4', 'userid_4'),
  (5, 'description_5', 5, 5, 'name_5', 'type_5', 'userid_5');

TRUNCATE TABLE "polestar"."cmm_service";
INSERT INTO "polestar"."cmm_service" ("id", "ctime", "checkavailability", "dtime", "description", "mtime", "service_name") VALUES
  (1, 1, 1, 1, 'description_1', 1, 'service_name_1'),
  (2, 2, 2, 2, 'description_2', 2, 'service_name_2'),
  (3, 3, 3, 3, 'description_3', 3, 'service_name_3'),
  (4, 4, 4, 4, 'description_4', 4, 'service_name_4'),
  (5, 5, 5, 5, 'description_5', 5, 'service_name_5');

TRUNCATE TABLE "polestar"."cmm_service_associate";
INSERT INTO "polestar"."cmm_service_associate" ("resource_id", "service_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cmm_trait_history_202507";
INSERT INTO "polestar"."cmm_trait_history_202507" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_202508";
INSERT INTO "polestar"."cmm_trait_history_202508" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_202509";
INSERT INTO "polestar"."cmm_trait_history_202509" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_202510";
INSERT INTO "polestar"."cmm_trait_history_202510" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_202511";
INSERT INTO "polestar"."cmm_trait_history_202511" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_202512";
INSERT INTO "polestar"."cmm_trait_history_202512" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_202601";
INSERT INTO "polestar"."cmm_trait_history_202601" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_202602";
INSERT INTO "polestar"."cmm_trait_history_202602" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_202603";
INSERT INTO "polestar"."cmm_trait_history_202603" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_202604";
INSERT INTO "polestar"."cmm_trait_history_202604" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_202605";
INSERT INTO "polestar"."cmm_trait_history_202605" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_202606";
INSERT INTO "polestar"."cmm_trait_history_202606" ("resource_id", "definition_id", "time_stamp", "string_value") VALUES
  (1, 1, 1, 'string_value_1'),
  (2, 2, 2, 'string_value_2'),
  (3, 3, 3, 'string_value_3'),
  (4, 4, 4, 'string_value_4'),
  (5, 5, 5, 'string_value_5');

TRUNCATE TABLE "polestar"."cmm_trait_history_table_info";
INSERT INTO "polestar"."cmm_trait_history_table_info" ("table_name", "create_date", "table_type", "include_view") VALUES
  ('table_name_1', 'create_date_1', 'table_type_1', 1),
  ('table_name_2', 'create_date_2', 'table_type_2', 2),
  ('table_name_3', 'create_date_3', 'table_type_3', 3),
  ('table_name_4', 'create_date_4', 'table_type_4', 4),
  ('table_name_5', 'create_date_5', 'table_type_5', 5);

TRUNCATE TABLE "polestar"."command_restricted";
INSERT INTO "polestar"."command_restricted" ("id", "command", "command_desc", "is_enabled", "is_patterned") VALUES
  (1, 'command_1', 'command_desc_1', 1, 1),
  (2, 'command_2', 'command_desc_2', 2, 2),
  (3, 'command_3', 'command_desc_3', 3, 3),
  (4, 'command_4', 'command_desc_4', 4, 4),
  (5, 'command_5', 'command_desc_5', 5, 5);

TRUNCATE TABLE "polestar"."core_bookmark";
INSERT INTO "polestar"."core_bookmark" ("id", "domain_context", "bookmark_domin", "is_group", "name", "is_shared", "user_id", "parent_bookmark_id") VALUES
  (1, 'domain_context_1', 'bookmark_domin_1', 1, 'name_1', 1, 'user_id_1', 1),
  (2, 'domain_context_2', 'bookmark_domin_2', 2, 'name_2', 2, 'user_id_2', 2),
  (3, 'domain_context_3', 'bookmark_domin_3', 3, 'name_3', 3, 'user_id_3', 3),
  (4, 'domain_context_4', 'bookmark_domin_4', 4, 'name_4', 4, 'user_id_4', 4),
  (5, 'domain_context_5', 'bookmark_domin_5', 5, 'name_5', 5, 'user_id_5', 5);

TRUNCATE TABLE "polestar"."core_config";
INSERT INTO "polestar"."core_config" ("id", "mtime", "conf_def_id") VALUES
  (1, 1, 1),
  (2, 2, 2),
  (3, 3, 3),
  (4, 4, 4),
  (5, 5, 5);

TRUNCATE TABLE "polestar"."core_config_def";
INSERT INTO "polestar"."core_config_def" ("id") VALUES
  (1),
  (2),
  (3),
  (4),
  (5);

TRUNCATE TABLE "polestar"."core_config_history";
INSERT INTO "polestar"."core_config_history" ("dtype", "configuration_id", "ctime", "is_current", "errormessage", "mtime", "updatestatus", "userid", "resource_id") VALUES
  ('dtype_1', 1, 1, 1, 'errormessage_1', 1, 'updatestatus_1', 'userid_1', 1),
  ('dtype_2', 2, 2, 2, 'errormessage_2', 2, 'updatestatus_2', 'userid_2', 2),
  ('dtype_3', 3, 3, 3, 'errormessage_3', 3, 'updatestatus_3', 'userid_3', 3),
  ('dtype_4', 4, 4, 4, 'errormessage_4', 4, 'updatestatus_4', 'userid_4', 4),
  ('dtype_5', 5, 5, 5, 'errormessage_5', 5, 'updatestatus_5', 'userid_5', 5);

TRUNCATE TABLE "polestar"."core_default_job";
INSERT INTO "polestar"."core_default_job" ("id", "job_group", "job_name", "is_system") VALUES
  (1, 'job_group_1', 'job_name_1', 1),
  (2, 'job_group_2', 'job_name_2', 2),
  (3, 'job_group_3', 'job_name_3', 3),
  (4, 'job_group_4', 'job_name_4', 4),
  (5, 'job_group_5', 'job_name_5', 5);

TRUNCATE TABLE "polestar"."core_event_task";
INSERT INTO "polestar"."core_event_task" ("id", "ctime", "eventtype", "managerid") VALUES
  (1, 1, 'eventtype_1', 'managerid_1'),
  (2, 2, 'eventtype_2', 'managerid_2'),
  (3, 3, 'eventtype_3', 'managerid_3'),
  (4, 4, 'eventtype_4', 'managerid_4'),
  (5, 5, 'eventtype_5', 'managerid_5');

TRUNCATE TABLE "polestar"."core_event_task_prop";
INSERT INTO "polestar"."core_event_task_prop" ("id", "name", "stringvalue_short", "eventtask_id") VALUES
  (1, 'name_1', 'stringvalue_short_1', 1),
  (2, 'name_2', 'stringvalue_short_2', 2),
  (3, 'name_3', 'stringvalue_short_3', 3),
  (4, 'name_4', 'stringvalue_short_4', 4),
  (5, 'name_5', 'stringvalue_short_5', 5);

TRUNCATE TABLE "polestar"."core_manager_state";
INSERT INTO "polestar"."core_manager_state" ("managerid", "activeport", "is_client", "is_down", "ipaddress", "lastcheckintime", "macaddress", "is_master", "master_role", "is_patch", "port", "zoneid", "ctime") VALUES
  ('managerid_1', 1, 1, 1, 'ipaddress_1', 1, 'macaddress_1', 1, 1, 1, 1, 'zoneid_1', 1),
  ('managerid_2', 2, 2, 2, 'ipaddress_2', 2, 'macaddress_2', 2, 2, 2, 2, 'zoneid_2', 2),
  ('managerid_3', 3, 3, 3, 'ipaddress_3', 3, 'macaddress_3', 3, 3, 3, 3, 'zoneid_3', 3),
  ('managerid_4', 4, 4, 4, 'ipaddress_4', 4, 'macaddress_4', 4, 4, 4, 4, 'zoneid_4', 4),
  ('managerid_5', 5, 5, 5, 'ipaddress_5', 5, 'macaddress_5', 5, 5, 5, 5, 'zoneid_5', 5);

TRUNCATE TABLE "polestar"."core_noti_history_2026_15";
INSERT INTO "polestar"."core_noti_history_2026_15" ("alarmid", "ntime", "notitype", "resultmessage", "sourcename", "success", "userid", "username", "definitionid", "resourceid") VALUES
  (1, '2026-06-01 09:00:00', 1, 'resultmessage_1', 'sourcename_1', 1, 'userid_1', 'username_1', 1, 1),
  (2, '2026-06-02 09:00:00', 2, 'resultmessage_2', 'sourcename_2', 2, 'userid_2', 'username_2', 2, 2),
  (3, '2026-06-03 09:00:00', 3, 'resultmessage_3', 'sourcename_3', 3, 'userid_3', 'username_3', 3, 3),
  (4, '2026-06-04 09:00:00', 4, 'resultmessage_4', 'sourcename_4', 4, 'userid_4', 'username_4', 4, 4),
  (5, '2026-06-05 09:00:00', 5, 'resultmessage_5', 'sourcename_5', 5, 'userid_5', 'username_5', 5, 5);

TRUNCATE TABLE "polestar"."core_noti_history_2026_16";
INSERT INTO "polestar"."core_noti_history_2026_16" ("alarmid", "ntime", "notitype", "resultmessage", "sourcename", "success", "userid", "username", "definitionid", "resourceid") VALUES
  (1, '2026-06-01 09:00:00', 1, 'resultmessage_1', 'sourcename_1', 1, 'userid_1', 'username_1', 1, 1),
  (2, '2026-06-02 09:00:00', 2, 'resultmessage_2', 'sourcename_2', 2, 'userid_2', 'username_2', 2, 2),
  (3, '2026-06-03 09:00:00', 3, 'resultmessage_3', 'sourcename_3', 3, 'userid_3', 'username_3', 3, 3),
  (4, '2026-06-04 09:00:00', 4, 'resultmessage_4', 'sourcename_4', 4, 'userid_4', 'username_4', 4, 4),
  (5, '2026-06-05 09:00:00', 5, 'resultmessage_5', 'sourcename_5', 5, 'userid_5', 'username_5', 5, 5);

TRUNCATE TABLE "polestar"."core_noti_history_2026_17";
INSERT INTO "polestar"."core_noti_history_2026_17" ("alarmid", "ntime", "notitype", "resultmessage", "sourcename", "success", "userid", "username", "definitionid", "resourceid") VALUES
  (1, '2026-06-01 09:00:00', 1, 'resultmessage_1', 'sourcename_1', 1, 'userid_1', 'username_1', 1, 1),
  (2, '2026-06-02 09:00:00', 2, 'resultmessage_2', 'sourcename_2', 2, 'userid_2', 'username_2', 2, 2),
  (3, '2026-06-03 09:00:00', 3, 'resultmessage_3', 'sourcename_3', 3, 'userid_3', 'username_3', 3, 3),
  (4, '2026-06-04 09:00:00', 4, 'resultmessage_4', 'sourcename_4', 4, 'userid_4', 'username_4', 4, 4),
  (5, '2026-06-05 09:00:00', 5, 'resultmessage_5', 'sourcename_5', 5, 'userid_5', 'username_5', 5, 5);

TRUNCATE TABLE "polestar"."core_noti_history_2026_18";
INSERT INTO "polestar"."core_noti_history_2026_18" ("alarmid", "ntime", "notitype", "resultmessage", "sourcename", "success", "userid", "username", "definitionid", "resourceid") VALUES
  (1, '2026-06-01 09:00:00', 1, 'resultmessage_1', 'sourcename_1', 1, 'userid_1', 'username_1', 1, 1),
  (2, '2026-06-02 09:00:00', 2, 'resultmessage_2', 'sourcename_2', 2, 'userid_2', 'username_2', 2, 2),
  (3, '2026-06-03 09:00:00', 3, 'resultmessage_3', 'sourcename_3', 3, 'userid_3', 'username_3', 3, 3),
  (4, '2026-06-04 09:00:00', 4, 'resultmessage_4', 'sourcename_4', 4, 'userid_4', 'username_4', 4, 4),
  (5, '2026-06-05 09:00:00', 5, 'resultmessage_5', 'sourcename_5', 5, 'userid_5', 'username_5', 5, 5);

TRUNCATE TABLE "polestar"."core_noti_history_2026_19";
INSERT INTO "polestar"."core_noti_history_2026_19" ("alarmid", "ntime", "notitype", "resultmessage", "sourcename", "success", "userid", "username", "definitionid", "resourceid") VALUES
  (1, '2026-06-01 09:00:00', 1, 'resultmessage_1', 'sourcename_1', 1, 'userid_1', 'username_1', 1, 1),
  (2, '2026-06-02 09:00:00', 2, 'resultmessage_2', 'sourcename_2', 2, 'userid_2', 'username_2', 2, 2),
  (3, '2026-06-03 09:00:00', 3, 'resultmessage_3', 'sourcename_3', 3, 'userid_3', 'username_3', 3, 3),
  (4, '2026-06-04 09:00:00', 4, 'resultmessage_4', 'sourcename_4', 4, 'userid_4', 'username_4', 4, 4),
  (5, '2026-06-05 09:00:00', 5, 'resultmessage_5', 'sourcename_5', 5, 'userid_5', 'username_5', 5, 5);

TRUNCATE TABLE "polestar"."core_noti_history_2026_20";
INSERT INTO "polestar"."core_noti_history_2026_20" ("alarmid", "ntime", "notitype", "resultmessage", "sourcename", "success", "userid", "username", "definitionid", "resourceid") VALUES
  (1, '2026-06-01 09:00:00', 1, 'resultmessage_1', 'sourcename_1', 1, 'userid_1', 'username_1', 1, 1),
  (2, '2026-06-02 09:00:00', 2, 'resultmessage_2', 'sourcename_2', 2, 'userid_2', 'username_2', 2, 2),
  (3, '2026-06-03 09:00:00', 3, 'resultmessage_3', 'sourcename_3', 3, 'userid_3', 'username_3', 3, 3),
  (4, '2026-06-04 09:00:00', 4, 'resultmessage_4', 'sourcename_4', 4, 'userid_4', 'username_4', 4, 4),
  (5, '2026-06-05 09:00:00', 5, 'resultmessage_5', 'sourcename_5', 5, 'userid_5', 'username_5', 5, 5);

TRUNCATE TABLE "polestar"."core_noti_history_2026_21";
INSERT INTO "polestar"."core_noti_history_2026_21" ("alarmid", "ntime", "notitype", "resultmessage", "sourcename", "success", "userid", "username", "definitionid", "resourceid") VALUES
  (1, '2026-06-01 09:00:00', 1, 'resultmessage_1', 'sourcename_1', 1, 'userid_1', 'username_1', 1, 1),
  (2, '2026-06-02 09:00:00', 2, 'resultmessage_2', 'sourcename_2', 2, 'userid_2', 'username_2', 2, 2),
  (3, '2026-06-03 09:00:00', 3, 'resultmessage_3', 'sourcename_3', 3, 'userid_3', 'username_3', 3, 3),
  (4, '2026-06-04 09:00:00', 4, 'resultmessage_4', 'sourcename_4', 4, 'userid_4', 'username_4', 4, 4),
  (5, '2026-06-05 09:00:00', 5, 'resultmessage_5', 'sourcename_5', 5, 'userid_5', 'username_5', 5, 5);

TRUNCATE TABLE "polestar"."core_noti_history_2026_22";
INSERT INTO "polestar"."core_noti_history_2026_22" ("alarmid", "ntime", "notitype", "resultmessage", "sourcename", "success", "userid", "username", "definitionid", "resourceid") VALUES
  (1, '2026-06-01 09:00:00', 1, 'resultmessage_1', 'sourcename_1', 1, 'userid_1', 'username_1', 1, 1),
  (2, '2026-06-02 09:00:00', 2, 'resultmessage_2', 'sourcename_2', 2, 'userid_2', 'username_2', 2, 2),
  (3, '2026-06-03 09:00:00', 3, 'resultmessage_3', 'sourcename_3', 3, 'userid_3', 'username_3', 3, 3),
  (4, '2026-06-04 09:00:00', 4, 'resultmessage_4', 'sourcename_4', 4, 'userid_4', 'username_4', 4, 4),
  (5, '2026-06-05 09:00:00', 5, 'resultmessage_5', 'sourcename_5', 5, 'userid_5', 'username_5', 5, 5);

TRUNCATE TABLE "polestar"."core_noti_history_tables";
INSERT INTO "polestar"."core_noti_history_tables" ("table_name", "create_date", "table_type") VALUES
  ('table_name_1', 'create_date_1', 'table_type_1'),
  ('table_name_2', 'create_date_2', 'table_type_2'),
  ('table_name_3', 'create_date_3', 'table_type_3'),
  ('table_name_4', 'create_date_4', 'table_type_4'),
  ('table_name_5', 'create_date_5', 'table_type_5');

TRUNCATE TABLE "polestar"."core_popup_noti";
INSERT INTO "polestar"."core_popup_noti" ("id", "alarmctime", "alarmid", "alarmname", "alarmseverityname", "message", "resourceid", "senttime", "source", "userid", "username") VALUES
  (1, 1, 1, 'alarmname_1', 'alarmseverityname_1', 'message_1', 1, 1, 'source_1', 'userid_1', 'username_1'),
  (2, 2, 2, 'alarmname_2', 'alarmseverityname_2', 'message_2', 2, 2, 'source_2', 'userid_2', 'username_2'),
  (3, 3, 3, 'alarmname_3', 'alarmseverityname_3', 'message_3', 3, 3, 'source_3', 'userid_3', 'username_3'),
  (4, 4, 4, 'alarmname_4', 'alarmseverityname_4', 'message_4', 4, 4, 'source_4', 'userid_4', 'username_4'),
  (5, 5, 5, 'alarmname_5', 'alarmseverityname_5', 'message_5', 5, 5, 'source_5', 'userid_5', 'username_5');

TRUNCATE TABLE "polestar"."core_property_def";
INSERT INTO "polestar"."core_property_def" ("dtype", "id", "activationpolicy", "is_deleted", "description", "displayname", "groupname", "name", "propertyorder", "readonly", "is_searchable", "summary", "auto_complete_domain", "blockname", "cipher", "defaultvalue", "propertytype", "regexpvalidator", "regexpvalidatormessage", "required", "selectquery", "validators", "configurationdefinition_id", "parent_map_id", "memberproperty_id") VALUES
  ('dtype_1', 1, 1, 1, 'description_1', 'displayname_1', 'groupname_1', 'name_1', 1, 1, 1, 1, 'auto_complete_domain_1', 'blockname_1', 1, 'defaultvalue_1', 1, 'regexpvalidator_1', 'regexpvalidatormessage_1', 1, 'selectquery_1', 'validators_1', 1, 1, 1),
  ('dtype_2', 2, 2, 2, 'description_2', 'displayname_2', 'groupname_2', 'name_2', 2, 2, 2, 2, 'auto_complete_domain_2', 'blockname_2', 2, 'defaultvalue_2', 2, 'regexpvalidator_2', 'regexpvalidatormessage_2', 2, 'selectquery_2', 'validators_2', 2, 2, 2),
  ('dtype_3', 3, 3, 3, 'description_3', 'displayname_3', 'groupname_3', 'name_3', 3, 3, 3, 3, 'auto_complete_domain_3', 'blockname_3', 3, 'defaultvalue_3', 3, 'regexpvalidator_3', 'regexpvalidatormessage_3', 3, 'selectquery_3', 'validators_3', 3, 3, 3),
  ('dtype_4', 4, 4, 4, 'description_4', 'displayname_4', 'groupname_4', 'name_4', 4, 4, 4, 4, 'auto_complete_domain_4', 'blockname_4', 4, 'defaultvalue_4', 4, 'regexpvalidator_4', 'regexpvalidatormessage_4', 4, 'selectquery_4', 'validators_4', 4, 4, 4),
  ('dtype_5', 5, 5, 5, 'description_5', 'displayname_5', 'groupname_5', 'name_5', 5, 5, 5, 5, 'auto_complete_domain_5', 'blockname_5', 5, 'defaultvalue_5', 5, 'regexpvalidator_5', 'regexpvalidatormessage_5', 5, 'selectquery_5', 'validators_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."core_schema_version";
INSERT INTO "polestar"."core_schema_version" ("installed_rank", "version", "description", "type", "script", "checksum", "installed_by", "installed_on", "execution_time", "success") VALUES
  (1, 'version_1', 'description_1', 'type_1', 'script_1', 1, 'installed_by_1', '2026-06-01 09:00:00', 1, true),
  (2, 'version_2', 'description_2', 'type_2', 'script_2', 2, 'installed_by_2', '2026-06-02 09:00:00', 2, false),
  (3, 'version_3', 'description_3', 'type_3', 'script_3', 3, 'installed_by_3', '2026-06-03 09:00:00', 3, true),
  (4, 'version_4', 'description_4', 'type_4', 'script_4', 4, 'installed_by_4', '2026-06-04 09:00:00', 4, false),
  (5, 'version_5', 'description_5', 'type_5', 'script_5', 5, 'installed_by_5', '2026-06-05 09:00:00', 5, true);

TRUNCATE TABLE "polestar"."core_system_config";
INSERT INTO "polestar"."core_system_config" ("id", "category", "configurationname", "description", "displayname", "icon", "managergroup", "managername", "optlock", "settingpolicy", "configuration_id") VALUES
  ('id_1', 'category_1', 'configurationname_1', 'description_1', 'displayname_1', 'icon_1', 'managergroup_1', 'managername_1', 1, 1, 1),
  ('id_2', 'category_2', 'configurationname_2', 'description_2', 'displayname_2', 'icon_2', 'managergroup_2', 'managername_2', 2, 2, 2),
  ('id_3', 'category_3', 'configurationname_3', 'description_3', 'displayname_3', 'icon_3', 'managergroup_3', 'managername_3', 3, 3, 3),
  ('id_4', 'category_4', 'configurationname_4', 'description_4', 'displayname_4', 'icon_4', 'managergroup_4', 'managername_4', 4, 4, 4),
  ('id_5', 'category_5', 'configurationname_5', 'description_5', 'displayname_5', 'icon_5', 'managergroup_5', 'managername_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."cust_prop_def_select";
INSERT INTO "polestar"."cust_prop_def_select" ("property_def_id", "selectlist") VALUES
  (1, 'selectlist_1'),
  (2, 'selectlist_2'),
  (3, 'selectlist_3'),
  (4, 'selectlist_4'),
  (5, 'selectlist_5');

TRUNCATE TABLE "polestar"."cust_property";
INSERT INTO "polestar"."cust_property" ("id", "definition_id", "is_error", "errormessage", "numericvalue", "stringvalue", "time_stamp", "is_user_edited", "resource_id") VALUES
  (1, 1, 1, 'errormessage_1', 1.5, 'stringvalue_1', 1, 1, 1),
  (2, 2, 2, 'errormessage_2', 2.5, 'stringvalue_2', 2, 2, 2),
  (3, 3, 3, 'errormessage_3', 3.5, 'stringvalue_3', 3, 3, 3),
  (4, 4, 4, 'errormessage_4', 4.5, 'stringvalue_4', 4, 4, 4),
  (5, 5, 5, 'errormessage_5', 5.5, 'stringvalue_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."cust_property_def";
INSERT INTO "polestar"."cust_property_def" ("id", "ctime", "defaultvalue", "description", "editpolicy", "name", "prop_expression", "propertytype", "required", "is_summary", "target_resource_type") VALUES
  (1, 1, 'defaultvalue_1', 'description_1', 'editpolicy_1', 'name_1', 'prop_expression_1', 'propertytype_1', 1, 1, 'target_resource_type_1'),
  (2, 2, 'defaultvalue_2', 'description_2', 'editpolicy_2', 'name_2', 'prop_expression_2', 'propertytype_2', 2, 2, 'target_resource_type_2'),
  (3, 3, 'defaultvalue_3', 'description_3', 'editpolicy_3', 'name_3', 'prop_expression_3', 'propertytype_3', 3, 3, 'target_resource_type_3'),
  (4, 4, 'defaultvalue_4', 'description_4', 'editpolicy_4', 'name_4', 'prop_expression_4', 'propertytype_4', 4, 4, 'target_resource_type_4'),
  (5, 5, 'defaultvalue_5', 'description_5', 'editpolicy_5', 'name_5', 'prop_expression_5', 'propertytype_5', 5, 5, 'target_resource_type_5');

TRUNCATE TABLE "polestar"."cust_table";
INSERT INTO "polestar"."cust_table" ("id", "ctime", "description", "is_inconsistencty", "name", "target_resource_type") VALUES
  (1, 1, 'description_1', 1, 'name_1', 'target_resource_type_1'),
  (2, 2, 'description_2', 2, 'name_2', 'target_resource_type_2'),
  (3, 3, 'description_3', 3, 'name_3', 'target_resource_type_3'),
  (4, 4, 'description_4', 4, 'name_4', 'target_resource_type_4'),
  (5, 5, 'description_5', 5, 'name_5', 'target_resource_type_5');

TRUNCATE TABLE "polestar"."cust_table_column";
INSERT INTO "polestar"."cust_table_column" ("id", "columnorder", "columntype", "is_editable", "expression_str", "name", "uuid", "property_def_id", "table_id") VALUES
  (1, 1, 'columntype_1', 1, 'expression_str_1', 'name_1', 'uuid_1', 1, 1),
  (2, 2, 'columntype_2', 2, 'expression_str_2', 'name_2', 'uuid_2', 2, 2),
  (3, 3, 'columntype_3', 3, 'expression_str_3', 'name_3', 'uuid_3', 3, 3),
  (4, 4, 'columntype_4', 4, 'expression_str_4', 'name_4', 'uuid_4', 4, 4),
  (5, 5, 'columntype_5', 5, 'expression_str_5', 'name_5', 'uuid_5', 5, 5);

TRUNCATE TABLE "polestar"."cust_table_exception";
INSERT INTO "polestar"."cust_table_exception" ("table_id", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cust_table_target";
INSERT INTO "polestar"."cust_table_target" ("table_id", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cv_graph_template";
INSERT INTO "polestar"."cv_graph_template" ("id", "description", "name", "user_id") VALUES
  (1, 'description_1', 'name_1', 'user_id_1'),
  (2, 'description_2', 'name_2', 'user_id_2'),
  (3, 'description_3', 'name_3', 'user_id_3'),
  (4, 'description_4', 'name_4', 'user_id_4'),
  (5, 'description_5', 'name_5', 'user_id_5');

TRUNCATE TABLE "polestar"."cv_line_template";
INSERT INTO "polestar"."cv_line_template" ("id", "line_color", "data_type", "graph_type", "definition_id", "resource_type", "is_view", "graph_template_id") VALUES
  (1, 'line_color_1', 'data_type_1', 'graph_type_1', 1, 'resource_type_1', 1, 1),
  (2, 'line_color_2', 'data_type_2', 'graph_type_2', 2, 'resource_type_2', 2, 2),
  (3, 'line_color_3', 'data_type_3', 'graph_type_3', 3, 'resource_type_3', 3, 3),
  (4, 'line_color_4', 'data_type_4', 'graph_type_4', 4, 'resource_type_4', 4, 4),
  (5, 'line_color_5', 'data_type_5', 'graph_type_5', 5, 'resource_type_5', 5, 5);

TRUNCATE TABLE "polestar"."cv_search_condition";
INSERT INTO "polestar"."cv_search_condition" ("id", "columnsize", "description", "enddate", "fromdate", "graphtitle", "name", "pagesize", "resourcestatus", "searchperiod", "user_id") VALUES
  (1, 1, 'description_1', 1, 1, 'graphtitle_1', 'name_1', 1, 1, 1, 'user_id_1'),
  (2, 2, 'description_2', 2, 2, 'graphtitle_2', 'name_2', 2, 2, 2, 'user_id_2'),
  (3, 3, 'description_3', 3, 3, 'graphtitle_3', 'name_3', 3, 3, 3, 'user_id_3'),
  (4, 4, 'description_4', 4, 4, 'graphtitle_4', 'name_4', 4, 4, 4, 'user_id_4'),
  (5, 5, 'description_5', 5, 5, 'graphtitle_5', 'name_5', 5, 5, 5, 'user_id_5');

TRUNCATE TABLE "polestar"."cv_search_condition_resource";
INSERT INTO "polestar"."cv_search_condition_resource" ("condition_id", "resources") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."cv_search_condition_template";
INSERT INTO "polestar"."cv_search_condition_template" ("condition_id", "graphtemplates") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."dashboard_slide";
INSERT INTO "polestar"."dashboard_slide" ("id", "description", "intervalminutes", "intervalseconds", "name", "is_use", "user_id") VALUES
  (1, 'description_1', 1, 1, 'name_1', 1, 'user_id_1'),
  (2, 'description_2', 2, 2, 'name_2', 2, 'user_id_2'),
  (3, 'description_3', 3, 3, 'name_3', 3, 'user_id_3'),
  (4, 'description_4', 4, 4, 'name_4', 4, 'user_id_4'),
  (5, 'description_5', 5, 5, 'name_5', 5, 'user_id_5');

TRUNCATE TABLE "polestar"."dashboard_slide_select";
INSERT INTO "polestar"."dashboard_slide_select" ("dashboardslide_id", "dashboard_id", "dashboard_order") VALUES
  (1, 1, 1),
  (2, 2, 2),
  (3, 3, 3),
  (4, 4, 4),
  (5, 5, 5);

TRUNCATE TABLE "polestar"."db_attached_gadget";
INSERT INTO "polestar"."db_attached_gadget" ("id", "is_collapsed", "color", "column_num", "is_configured", "refreshinterval", "row_order", "size_x", "size_y", "title", "configuration_id", "dashboard_id", "gadget_key") VALUES
  (1, 1, 'color_1', 1, 1, 1, 1, 1, 1, 'title_1', 1, 1, 'gadget_key_1'),
  (2, 2, 'color_2', 2, 2, 2, 2, 2, 2, 'title_2', 2, 2, 'gadget_key_2'),
  (3, 3, 'color_3', 3, 3, 3, 3, 3, 3, 'title_3', 3, 3, 'gadget_key_3'),
  (4, 4, 'color_4', 4, 4, 4, 4, 4, 4, 'title_4', 4, 4, 'gadget_key_4'),
  (5, 5, 'color_5', 5, 5, 5, 5, 5, 5, 'title_5', 5, 5, 'gadget_key_5');

TRUNCATE TABLE "polestar"."db_dashboard";
INSERT INTO "polestar"."db_dashboard" ("id", "acl_id", "description", "name", "owneruserid", "is_shared", "is_system", "uuid") VALUES
  (1, 1, 'description_1', 'name_1', 'owneruserid_1', 1, 1, 'uuid_1'),
  (2, 2, 'description_2', 'name_2', 'owneruserid_2', 2, 2, 'uuid_2'),
  (3, 3, 'description_3', 'name_3', 'owneruserid_3', 3, 3, 'uuid_3'),
  (4, 4, 'description_4', 'name_4', 'owneruserid_4', 4, 4, 'uuid_4'),
  (5, 5, 'description_5', 'name_5', 'owneruserid_5', 5, 5, 'uuid_5');

TRUNCATE TABLE "polestar"."db_dashboard_favor";
INSERT INTO "polestar"."db_dashboard_favor" ("user_id", "dashboard_id", "priority") VALUES
  ('user_id_1', 1, 1),
  ('user_id_2', 2, 2),
  ('user_id_3', 3, 3),
  ('user_id_4', 4, 4),
  ('user_id_5', 5, 5);

TRUNCATE TABLE "polestar"."db_gadget";
INSERT INTO "polestar"."db_gadget" ("gadget_key", "allowpanelrefresh", "blockname", "category", "configurationeditblockname", "description", "licensedomain", "licenseparts", "headericon", "name", "pagename", "thumbnailimagepath", "conf_def_id") VALUES
  ('gadget_key_1', 1, 'blockname_1', 'category_1', 'configurationeditblockname_1', 'description_1', 'licensedomain_1', 'licenseparts_1', 'headericon_1', 'name_1', 'pagename_1', 'thumbnailimagepath_1', 1),
  ('gadget_key_2', 2, 'blockname_2', 'category_2', 'configurationeditblockname_2', 'description_2', 'licensedomain_2', 'licenseparts_2', 'headericon_2', 'name_2', 'pagename_2', 'thumbnailimagepath_2', 2),
  ('gadget_key_3', 3, 'blockname_3', 'category_3', 'configurationeditblockname_3', 'description_3', 'licensedomain_3', 'licenseparts_3', 'headericon_3', 'name_3', 'pagename_3', 'thumbnailimagepath_3', 3),
  ('gadget_key_4', 4, 'blockname_4', 'category_4', 'configurationeditblockname_4', 'description_4', 'licensedomain_4', 'licenseparts_4', 'headericon_4', 'name_4', 'pagename_4', 'thumbnailimagepath_4', 4),
  ('gadget_key_5', 5, 'blockname_5', 'category_5', 'configurationeditblockname_5', 'description_5', 'licensedomain_5', 'licenseparts_5', 'headericon_5', 'name_5', 'pagename_5', 'thumbnailimagepath_5', 5);

TRUNCATE TABLE "polestar"."dpm_background_info";
INSERT INTO "polestar"."dpm_background_info" ("resource_id", "process_name", "pid") VALUES
  (1, 'process_name_1', 1),
  (2, 'process_name_2', 2),
  (3, 'process_name_3', 3),
  (4, 'process_name_4', 4),
  (5, 'process_name_5', 5);

TRUNCATE TABLE "polestar"."dpm_default_log_monitor";
INSERT INTO "polestar"."dpm_default_log_monitor" ("id", "apply", "casesensitive", "customencodingtype", "dbtype", "debug", "description", "encodingtype", "error", "fatal", "info", "last_updated_date", "logtype", "matchingtype", "name", "scantype", "version", "warn") VALUES
  (1, 1, 1, 'customencodingtype_1', 'dbtype_1', 'debug_1', 'description_1', 'encodingtype_1', 'error_1', 'fatal_1', 'info_1', 1, 'logtype_1', 'matchingtype_1', 'name_1', 'scantype_1', 'version_1', 'warn_1'),
  (2, 2, 2, 'customencodingtype_2', 'dbtype_2', 'debug_2', 'description_2', 'encodingtype_2', 'error_2', 'fatal_2', 'info_2', 2, 'logtype_2', 'matchingtype_2', 'name_2', 'scantype_2', 'version_2', 'warn_2'),
  (3, 3, 3, 'customencodingtype_3', 'dbtype_3', 'debug_3', 'description_3', 'encodingtype_3', 'error_3', 'fatal_3', 'info_3', 3, 'logtype_3', 'matchingtype_3', 'name_3', 'scantype_3', 'version_3', 'warn_3'),
  (4, 4, 4, 'customencodingtype_4', 'dbtype_4', 'debug_4', 'description_4', 'encodingtype_4', 'error_4', 'fatal_4', 'info_4', 4, 'logtype_4', 'matchingtype_4', 'name_4', 'scantype_4', 'version_4', 'warn_4'),
  (5, 5, 5, 'customencodingtype_5', 'dbtype_5', 'debug_5', 'description_5', 'encodingtype_5', 'error_5', 'fatal_5', 'info_5', 5, 'logtype_5', 'matchingtype_5', 'name_5', 'scantype_5', 'version_5', 'warn_5');

TRUNCATE TABLE "polestar"."dpm_enqueue";
INSERT INTO "polestar"."dpm_enqueue" ("resource_id", "name", "type", "reason", "event", "fail_request", "request", "succ_request", "wait", "wait_time") VALUES
  (1, 'name_1', 'type_1', 'reason_1', 'event_1', 1, 1, 1, 1, 1),
  (2, 'name_2', 'type_2', 'reason_2', 'event_2', 2, 2, 2, 2, 2),
  (3, 'name_3', 'type_3', 'reason_3', 'event_3', 3, 3, 3, 3, 3),
  (4, 'name_4', 'type_4', 'reason_4', 'event_4', 4, 4, 4, 4, 4),
  (5, 'name_5', 'type_5', 'reason_5', 'event_5', 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."dpm_latch";
INSERT INTO "polestar"."dpm_latch" ("resource_id", "name", "gets", "immediate_gets", "immediate_misses", "misses", "sleeps", "wait_holding_latch", "wait_time") VALUES
  (1, 'name_1', 1, 1, 1, 1, 1, 1, 1),
  (2, 'name_2', 2, 2, 2, 2, 2, 2, 2),
  (3, 'name_3', 3, 3, 3, 3, 3, 3, 3),
  (4, 'name_4', 4, 4, 4, 4, 4, 4, 4),
  (5, 'name_5', 5, 5, 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."dpm_query_custom_monitor";
INSERT INTO "polestar"."dpm_query_custom_monitor" ("is_response_time", "script_body", "resourcetype") VALUES
  (1, 'script_body_1', 'resourcetype_1'),
  (2, 'script_body_2', 'resourcetype_2'),
  (3, 'script_body_3', 'resourcetype_3'),
  (4, 'script_body_4', 'resourcetype_4'),
  (5, 'script_body_5', 'resourcetype_5');

TRUNCATE TABLE "polestar"."dpm_session_stat";
INSERT INTO "polestar"."dpm_session_stat" ("resource_id", "session_id", "serial", "blkchanges", "cputime", "dbtime", "executions", "hardparses", "loreads", "opencursor", "phyreads", "totalparses", "undosize") VALUES
  (1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1),
  (2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2),
  (3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3),
  (4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
  (5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."dpm_stat";
INSERT INTO "polestar"."dpm_stat" ("resource_id", "stat_id", "prevvalue", "time") VALUES
  (1, 1, 1, 1),
  (2, 2, 2, 2),
  (3, 3, 3, 3),
  (4, 4, 4, 4),
  (5, 5, 5, 5);

TRUNCATE TABLE "polestar"."dpm_topsql";
INSERT INTO "polestar"."dpm_topsql" ("resource_id", "sql_id", "time") VALUES
  (1, 'sql_id_1', 1),
  (2, 'sql_id_2', 2),
  (3, 'sql_id_3', 3),
  (4, 'sql_id_4', 4),
  (5, 'sql_id_5', 5);

TRUNCATE TABLE "polestar"."dpm_waitevent";
INSERT INTO "polestar"."dpm_waitevent" ("resource_id", "event_id", "cputime", "dbtime", "totaltimewaited", "totaltimeouts", "totalwaits") VALUES
  (1, 1, 1, 1, 1, 1, 1),
  (2, 2, 2, 2, 2, 2, 2),
  (3, 3, 3, 3, 3, 3, 3),
  (4, 4, 4, 4, 4, 4, 4),
  (5, 5, 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."es_index_info";
INSERT INTO "polestar"."es_index_info" ("id", "base_name", "class_name", "prev_index", "time_str") VALUES
  (1, 'base_name_1', 'class_name_1', 'prev_index_1', 'time_str_1'),
  (2, 'base_name_2', 'class_name_2', 'prev_index_2', 'time_str_2'),
  (3, 'base_name_3', 'class_name_3', 'prev_index_3', 'time_str_3'),
  (4, 'base_name_4', 'class_name_4', 'prev_index_4', 'time_str_4'),
  (5, 'base_name_5', 'class_name_5', 'prev_index_5', 'time_str_5');

TRUNCATE TABLE "polestar"."es_index_setting";
INSERT INTO "polestar"."es_index_setting" ("id", "amount", "description", "replicas", "shards", "indexingtype") VALUES
  ('id_1', 1, 'description_1', 1, 1, 1),
  ('id_2', 2, 'description_2', 2, 2, 2),
  ('id_3', 3, 'description_3', 3, 3, 3),
  ('id_4', 4, 'description_4', 4, 4, 4),
  ('id_5', 5, 'description_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."es_merge_history";
INSERT INTO "polestar"."es_merge_history" ("id", "db_query_time", "db_resource_size", "endtime", "es_index_time", "es_query_time", "es_request_time", "es_total_size", "groupid", "managerid", "merge_rule", "merge_type", "splitmode", "splitsize", "starttime", "totaltime") VALUES
  (1, 1, 1, '2026-06-01 09:00:00', 1, 1, 1, 1, 1, 'managerid_1', 'merge_rule_1', 'merge_type_1', 1, 1, '2026-06-01 09:00:00', 1),
  (2, 2, 2, '2026-06-02 09:00:00', 2, 2, 2, 2, 2, 'managerid_2', 'merge_rule_2', 'merge_type_2', 2, 2, '2026-06-02 09:00:00', 2),
  (3, 3, 3, '2026-06-03 09:00:00', 3, 3, 3, 3, 3, 'managerid_3', 'merge_rule_3', 'merge_type_3', 3, 3, '2026-06-03 09:00:00', 3),
  (4, 4, 4, '2026-06-04 09:00:00', 4, 4, 4, 4, 4, 'managerid_4', 'merge_rule_4', 'merge_type_4', 4, 4, '2026-06-04 09:00:00', 4),
  (5, 5, 5, '2026-06-05 09:00:00', 5, 5, 5, 5, 5, 'managerid_5', 'merge_rule_5', 'merge_type_5', 5, 5, '2026-06-05 09:00:00', 5);

TRUNCATE TABLE "polestar"."fault_event_log";
INSERT INTO "polestar"."fault_event_log" ("id", "lasttime") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."fileid_gen";
INSERT INTO "polestar"."fileid_gen" ("gen_name", "gen_val") VALUES
  ('gen_name_1', 1),
  ('gen_name_2', 2),
  ('gen_name_3', 3),
  ('gen_name_4', 4),
  ('gen_name_5', 5);

TRUNCATE TABLE "polestar"."flow_app_def";
INSERT INTO "polestar"."flow_app_def" ("id", "app_name", "def_ip", "def_iplong", "def_port", "receiver_id") VALUES
  (1, 'app_name_1', 'def_ip_1', 1, 1, 1),
  (2, 'app_name_2', 'def_ip_2', 2, 2, 2),
  (3, 'app_name_3', 'def_ip_3', 3, 3, 3),
  (4, 'app_name_4', 'def_ip_4', 4, 4, 4),
  (5, 'app_name_5', 'def_ip_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."flow_ipgroup_def";
INSERT INTO "polestar"."flow_ipgroup_def" ("id", "end_ip", "end_iplong", "ipgroup_name", "start_ip", "start_iplong") VALUES
  (1, 'end_ip_1', 1, 'ipgroup_name_1', 'start_ip_1', 1),
  (2, 'end_ip_2', 2, 'ipgroup_name_2', 'start_ip_2', 2),
  (3, 'end_ip_3', 3, 'ipgroup_name_3', 'start_ip_3', 3),
  (4, 'end_ip_4', 4, 'ipgroup_name_4', 'start_ip_4', 4),
  (5, 'end_ip_5', 5, 'ipgroup_name_5', 'start_ip_5', 5);

TRUNCATE TABLE "polestar"."hibernate_sequences";
INSERT INTO "polestar"."hibernate_sequences" ("sequence_name", "sequence_next_hi_value") VALUES
  ('sequence_name_1', 1),
  ('sequence_name_2', 2),
  ('sequence_name_3', 3),
  ('sequence_name_4', 4),
  ('sequence_name_5', 5);

TRUNCATE TABLE "polestar"."id_gen";
INSERT INTO "polestar"."id_gen" ("gen_name", "gen_val") VALUES
  ('gen_name_1', 1),
  ('gen_name_2', 2),
  ('gen_name_3', 3),
  ('gen_name_4', 4),
  ('gen_name_5', 5);

TRUNCATE TABLE "polestar"."ip_group";
INSERT INTO "polestar"."ip_group" ("id", "is_approved", "description", "name", "user_id", "user_name") VALUES
  (1, 1, 'description_1', 'name_1', 'user_id_1', 'user_name_1'),
  (2, 2, 'description_2', 'name_2', 'user_id_2', 'user_name_2'),
  (3, 3, 'description_3', 'name_3', 'user_id_3', 'user_name_3'),
  (4, 4, 'description_4', 'name_4', 'user_id_4', 'user_name_4'),
  (5, 5, 'description_5', 'name_5', 'user_id_5', 'user_name_5');

TRUNCATE TABLE "polestar"."ip_group_band";
INSERT INTO "polestar"."ip_group_band" ("id", "from_ipaddress", "from_ipaddress_long", "to_ipaddress", "to_ipaddress_long", "uuid", "group_id") VALUES
  (1, 'from_ipaddress_1', 1, 'to_ipaddress_1', 1, 'uuid_1', 1),
  (2, 'from_ipaddress_2', 2, 'to_ipaddress_2', 2, 'uuid_2', 2),
  (3, 'from_ipaddress_3', 3, 'to_ipaddress_3', 3, 'uuid_3', 3),
  (4, 'from_ipaddress_4', 4, 'to_ipaddress_4', 4, 'uuid_4', 4),
  (5, 'from_ipaddress_5', 5, 'to_ipaddress_5', 5, 'uuid_5', 5);

TRUNCATE TABLE "polestar"."ip_history";
INSERT INTO "polestar"."ip_history" ("id", "collect_resource_id", "historydetail", "histroy_time", "ip_address", "ip_address_long", "mac_address", "user_id", "user_name") VALUES
  (1, 1, 'historydetail_1', '2026-06-01 09:00:00', 'ip_address_1', 1, 'mac_address_1', 'user_id_1', 'user_name_1'),
  (2, 2, 'historydetail_2', '2026-06-02 09:00:00', 'ip_address_2', 2, 'mac_address_2', 'user_id_2', 'user_name_2'),
  (3, 3, 'historydetail_3', '2026-06-03 09:00:00', 'ip_address_3', 3, 'mac_address_3', 'user_id_3', 'user_name_3'),
  (4, 4, 'historydetail_4', '2026-06-04 09:00:00', 'ip_address_4', 4, 'mac_address_4', 'user_id_4', 'user_name_4'),
  (5, 5, 'historydetail_5', '2026-06-05 09:00:00', 'ip_address_5', 5, 'mac_address_5', 'user_id_5', 'user_name_5');

TRUNCATE TABLE "polestar"."ip_info";
INSERT INTO "polestar"."ip_info" ("id", "is_approved", "ctime", "collect_type", "dtime", "description", "ip_address", "ip_address_long", "mac_address", "modifiedtime", "modified_user_id", "ipam_add_type", "is_used", "user_id", "user_name", "collect_resource_id", "connection_resource_id", "group_id") VALUES
  (1, 1, 1, 'collect_type_1', 1, 'description_1', 'ip_address_1', 1, 'mac_address_1', 1, 'modified_user_id_1', 'ipam_add_type_1', 'is_used_1', 'user_id_1', 'user_name_1', 1, 1, 1),
  (2, 2, 2, 'collect_type_2', 2, 'description_2', 'ip_address_2', 2, 'mac_address_2', 2, 'modified_user_id_2', 'ipam_add_type_2', 'is_used_2', 'user_id_2', 'user_name_2', 2, 2, 2),
  (3, 3, 3, 'collect_type_3', 3, 'description_3', 'ip_address_3', 3, 'mac_address_3', 3, 'modified_user_id_3', 'ipam_add_type_3', 'is_used_3', 'user_id_3', 'user_name_3', 3, 3, 3),
  (4, 4, 4, 'collect_type_4', 4, 'description_4', 'ip_address_4', 4, 'mac_address_4', 4, 'modified_user_id_4', 'ipam_add_type_4', 'is_used_4', 'user_id_4', 'user_name_4', 4, 4, 4),
  (5, 5, 5, 'collect_type_5', 5, 'description_5', 'ip_address_5', 5, 'mac_address_5', 5, 'modified_user_id_5', 'ipam_add_type_5', 'is_used_5', 'user_id_5', 'user_name_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."k8s_event_sequence";
INSERT INTO "polestar"."k8s_event_sequence" ("k8s_resource_id", "last_event_time") VALUES
  ('k8s_resource_id_1', 1),
  ('k8s_resource_id_2', 2),
  ('k8s_resource_id_3', 3),
  ('k8s_resource_id_4', 4),
  ('k8s_resource_id_5', 5);

TRUNCATE TABLE "polestar"."k8s_resource_mapping";
INSERT INTO "polestar"."k8s_resource_mapping" ("resource_id", "target_name", "source_resource_type", "target_resource_type") VALUES
  (1, 'target_name_1', 'source_resource_type_1', 'target_resource_type_1'),
  (2, 'target_name_2', 'source_resource_type_2', 'target_resource_type_2'),
  (3, 'target_name_3', 'source_resource_type_3', 'target_resource_type_3'),
  (4, 'target_name_4', 'source_resource_type_4', 'target_resource_type_4'),
  (5, 'target_name_5', 'source_resource_type_5', 'target_resource_type_5');

TRUNCATE TABLE "polestar"."kb_metric_stat_avgmax";
INSERT INTO "polestar"."kb_metric_stat_avgmax" ("resource_id", "definition_name", "stat_date", "avg_of_max", "day_of_week", "hit_count", "hour_of_day", "max_val", "total_of_max") VALUES
  (1, 'definition_name_1', 'stat_date_1', 1.5, 1, 1, 1, 1.5, 1.5),
  (2, 'definition_name_2', 'stat_date_2', 2.5, 2, 2, 2, 2.5, 2.5),
  (3, 'definition_name_3', 'stat_date_3', 3.5, 3, 3, 3, 3.5, 3.5),
  (4, 'definition_name_4', 'stat_date_4', 4.5, 4, 4, 4, 4.5, 4.5),
  (5, 'definition_name_5', 'stat_date_5', 5.5, 5, 5, 5, 5.5, 5.5);

TRUNCATE TABLE "polestar"."lvw_link_info";
INSERT INTO "polestar"."lvw_link_info" ("id", "link_desc", "link_direction", "link_key", "link_type", "master_host_name", "link_name", "slave_host_name", "master_resource_id", "slave_resource_id") VALUES
  (1, 'link_desc_1', 'link_direction_1', 'link_key_1', 'link_type_1', 'master_host_name_1', 'link_name_1', 'slave_host_name_1', 1, 1),
  (2, 'link_desc_2', 'link_direction_2', 'link_key_2', 'link_type_2', 'master_host_name_2', 'link_name_2', 'slave_host_name_2', 2, 2),
  (3, 'link_desc_3', 'link_direction_3', 'link_key_3', 'link_type_3', 'master_host_name_3', 'link_name_3', 'slave_host_name_3', 3, 3),
  (4, 'link_desc_4', 'link_direction_4', 'link_key_4', 'link_type_4', 'master_host_name_4', 'link_name_4', 'slave_host_name_4', 4, 4),
  (5, 'link_desc_5', 'link_direction_5', 'link_key_5', 'link_type_5', 'master_host_name_5', 'link_name_5', 'slave_host_name_5', 5, 5);

TRUNCATE TABLE "polestar"."lvw_logical_link_info";
INSERT INTO "polestar"."lvw_logical_link_info" ("sub_link_key", "id", "logical_link_topology_id") VALUES
  ('sub_link_key_1', 1, 1),
  ('sub_link_key_2', 2, 2),
  ('sub_link_key_3', 3, 3),
  ('sub_link_key_4', 4, 4),
  ('sub_link_key_5', 5, 5);

TRUNCATE TABLE "polestar"."lvw_logical_link_list";
INSERT INTO "polestar"."lvw_logical_link_list" ("link_info_id", "logicallinks") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."lvw_logical_link_topology";
INSERT INTO "polestar"."lvw_logical_link_topology" ("id", "ctime", "completetime", "description", "logicalmapid", "mtime", "modifiedby", "name", "optlock", "is_running", "starttime", "userid", "role_id") VALUES
  (1, 1, 1, 'description_1', 1, 1, 'modifiedby_1', 'name_1', 1, 1, 1, 'userid_1', 1),
  (2, 2, 2, 'description_2', 2, 2, 'modifiedby_2', 'name_2', 2, 2, 2, 'userid_2', 2),
  (3, 3, 3, 'description_3', 3, 3, 'modifiedby_3', 'name_3', 3, 3, 3, 'userid_3', 3),
  (4, 4, 4, 'description_4', 4, 4, 'modifiedby_4', 'name_4', 4, 4, 4, 'userid_4', 4),
  (5, 5, 5, 'description_5', 5, 5, 'modifiedby_5', 'name_5', 5, 5, 5, 'userid_5', 5);

TRUNCATE TABLE "polestar"."lvw_script_info";
INSERT INTO "polestar"."lvw_script_info" ("id", "activated", "script", "resource_id") VALUES
  (1, 1, 'script_1', 1),
  (2, 2, 'script_2', 2),
  (3, 3, 'script_3', 3),
  (4, 4, 'script_4', 4),
  (5, 5, 'script_5', 5);

TRUNCATE TABLE "polestar"."lvw_virtual_link_info";
INSERT INTO "polestar"."lvw_virtual_link_info" ("bidir", "master_if_index", "policy", "slave_if_index", "sub_link_key", "switch_id", "switch_link_name", "virtual_wire", "id") VALUES
  (1, 1, 'policy_1', 1, 'sub_link_key_1', 1, 'switch_link_name_1', 1, 1),
  (2, 2, 'policy_2', 2, 'sub_link_key_2', 2, 'switch_link_name_2', 2, 2),
  (3, 3, 'policy_3', 3, 'sub_link_key_3', 3, 'switch_link_name_3', 3, 3),
  (4, 4, 'policy_4', 4, 'sub_link_key_4', 4, 'switch_link_name_4', 4, 4),
  (5, 5, 'policy_5', 5, 'sub_link_key_5', 5, 'switch_link_name_5', 5, 5);

TRUNCATE TABLE "polestar"."map_favor";
INSERT INTO "polestar"."map_favor" ("user_id", "map_id", "priority") VALUES
  ('user_id_1', 1, 1),
  ('user_id_2', 2, 2),
  ('user_id_3', 3, 3),
  ('user_id_4', 4, 4),
  ('user_id_5', 5, 5);

TRUNCATE TABLE "polestar"."map_link";
INSERT INTO "polestar"."map_link" ("id", "arrowfrom", "arrowto", "bundleexpanded", "ctime", "directiontype", "edgetype", "fontcolor", "fontsize", "linecolor", "linestroke", "linetype", "name", "sourceresourceshowtraffic", "targetresourceshowtraffic", "trafficcolor", "trafficfontsize", "zindex", "map_id", "source_node_id", "source_resource_id", "target_node_id", "target_resource_id") VALUES
  (1, 1, 1, 1, 1, 'directiontype_1', 'edgetype_1', 'fontcolor_1', 1, 'linecolor_1', 1.5, 'linetype_1', 'name_1', 1, 1, 'trafficcolor_1', 1, 1, 1, 1, 1, 1, 1),
  (2, 2, 2, 2, 2, 'directiontype_2', 'edgetype_2', 'fontcolor_2', 2, 'linecolor_2', 2.5, 'linetype_2', 'name_2', 2, 2, 'trafficcolor_2', 2, 2, 2, 2, 2, 2, 2),
  (3, 3, 3, 3, 3, 'directiontype_3', 'edgetype_3', 'fontcolor_3', 3, 'linecolor_3', 3.5, 'linetype_3', 'name_3', 3, 3, 'trafficcolor_3', 3, 3, 3, 3, 3, 3, 3),
  (4, 4, 4, 4, 4, 'directiontype_4', 'edgetype_4', 'fontcolor_4', 4, 'linecolor_4', 4.5, 'linetype_4', 'name_4', 4, 4, 'trafficcolor_4', 4, 4, 4, 4, 4, 4, 4),
  (5, 5, 5, 5, 5, 'directiontype_5', 'edgetype_5', 'fontcolor_5', 5, 'linecolor_5', 5.5, 'linetype_5', 'name_5', 5, 5, 'trafficcolor_5', 5, 5, 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."map_link_bandwidth_style";
INSERT INTO "polestar"."map_link_bandwidth_style" ("id", "bandwidth", "bandwidthraw", "color", "unit", "uuid", "width", "map_id") VALUES
  (1, 1, 1, 'color_1', 1, 'uuid_1', 1, 1),
  (2, 2, 2, 'color_2', 2, 'uuid_2', 2, 2),
  (3, 3, 3, 'color_3', 3, 'uuid_3', 3, 3),
  (4, 4, 4, 'color_4', 4, 'uuid_4', 4, 4),
  (5, 5, 5, 'color_5', 5, 'uuid_5', 5, 5);

TRUNCATE TABLE "polestar"."map_multi_range";
INSERT INTO "polestar"."map_multi_range" ("id", "backgroundcolor", "percentage", "rangeorder", "map_id") VALUES
  (1, 'backgroundcolor_1', 1, 1, 1),
  (2, 'backgroundcolor_2', 2, 2, 2),
  (3, 'backgroundcolor_3', 3, 3, 3),
  (4, 'backgroundcolor_4', 4, 4, 4),
  (5, 'backgroundcolor_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."map_node";
INSERT INTO "polestar"."map_node" ("id", "backgroundcolor", "backgroundimage", "ctime", "chartmeasurementdefinitionid_1", "chartmeasurementdefinitionid_2", "chartresourceid_1", "chartresourceid_2", "chartresourcetypename_1", "chartresourcetypename_2", "charttextsize", "description", "grouplinecolor", "height", "httpconnecturl", "httpsconnecturl", "icon", "innermapid", "isexpanded", "isgroup", "issubnetwork", "multiplechangeflag_1", "multiplechangeflag_2", "name", "nodetextcolor", "nodetextpos", "nodetextsize", "nodeviewchart", "parentctime", "rotate", "shownodechart", "subnetworkctime", "uiid", "width", "x_coord", "y_coord", "zindex", "map_id", "resource_id") VALUES
  (1, 'backgroundcolor_1', 'backgroundimage_1', 1, 1, 1, 1, 1, 'chartresourcetypename_1_1', 'chartresourcetypename_2_1', 1, 'description_1', 'grouplinecolor_1', 1, 'httpconnecturl_1', 'httpsconnecturl_1', 'icon_1', 1, 1, 1, 1, 1, 1, 'name_1', 'nodetextcolor_1', 'nodetextpos_1', 1, 1, 1, 1.5, 1, 1, 1, 1, 1, 1, 1, 1, 1),
  (2, 'backgroundcolor_2', 'backgroundimage_2', 2, 2, 2, 2, 2, 'chartresourcetypename_1_2', 'chartresourcetypename_2_2', 2, 'description_2', 'grouplinecolor_2', 2, 'httpconnecturl_2', 'httpsconnecturl_2', 'icon_2', 2, 2, 2, 2, 2, 2, 'name_2', 'nodetextcolor_2', 'nodetextpos_2', 2, 2, 2, 2.5, 2, 2, 2, 2, 2, 2, 2, 2, 2),
  (3, 'backgroundcolor_3', 'backgroundimage_3', 3, 3, 3, 3, 3, 'chartresourcetypename_1_3', 'chartresourcetypename_2_3', 3, 'description_3', 'grouplinecolor_3', 3, 'httpconnecturl_3', 'httpsconnecturl_3', 'icon_3', 3, 3, 3, 3, 3, 3, 'name_3', 'nodetextcolor_3', 'nodetextpos_3', 3, 3, 3, 3.5, 3, 3, 3, 3, 3, 3, 3, 3, 3),
  (4, 'backgroundcolor_4', 'backgroundimage_4', 4, 4, 4, 4, 4, 'chartresourcetypename_1_4', 'chartresourcetypename_2_4', 4, 'description_4', 'grouplinecolor_4', 4, 'httpconnecturl_4', 'httpsconnecturl_4', 'icon_4', 4, 4, 4, 4, 4, 4, 'name_4', 'nodetextcolor_4', 'nodetextpos_4', 4, 4, 4, 4.5, 4, 4, 4, 4, 4, 4, 4, 4, 4),
  (5, 'backgroundcolor_5', 'backgroundimage_5', 5, 5, 5, 5, 5, 'chartresourcetypename_1_5', 'chartresourcetypename_2_5', 5, 'description_5', 'grouplinecolor_5', 5, 'httpconnecturl_5', 'httpsconnecturl_5', 'icon_5', 5, 5, 5, 5, 5, 5, 'name_5', 'nodetextcolor_5', 'nodetextpos_5', 5, 5, 5, 5.5, 5, 5, 5, 5, 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."map_pathsegment";
INSERT INTO "polestar"."map_pathsegment" ("link_id", "x", "y") VALUES
  (1, 1, 1),
  (2, 2, 2),
  (3, 3, 3),
  (4, 4, 4),
  (5, 5, 5);

TRUNCATE TABLE "polestar"."map_slide_show";
INSERT INTO "polestar"."map_slide_show" ("userid", "intervalseconds") VALUES
  ('userid_1', 1),
  ('userid_2', 2),
  ('userid_3', 3),
  ('userid_4', 4),
  ('userid_5', 5);

TRUNCATE TABLE "polestar"."map_textlabel";
INSERT INTO "polestar"."map_textlabel" ("id", "backgroundcolor", "bordercolor", "borderstyle", "borderwidth", "ctime", "fontbold", "fontcolor", "fontfamily", "fontitalic", "fontsize", "height", "pattern", "rotate", "shadowblur", "shadowcolor", "shadowoffsetx", "shadowoffsety", "showtime", "subnetworkctime", "text", "width", "x_coord", "y_coord", "map_id", "textlabelpos") VALUES
  (1, 'backgroundcolor_1', 'bordercolor_1', 1, 1, 1, 1, 'fontcolor_1', 'fontfamily_1', 1, 1, 1, 'pattern_1', 1.5, 1, 'shadowcolor_1', 1, 1, 1, 1, 'text_1', 1, 1, 1, 1, 'textlabelpos_1'),
  (2, 'backgroundcolor_2', 'bordercolor_2', 2, 2, 2, 2, 'fontcolor_2', 'fontfamily_2', 2, 2, 2, 'pattern_2', 2.5, 2, 'shadowcolor_2', 2, 2, 2, 2, 'text_2', 2, 2, 2, 2, 'textlabelpos_2'),
  (3, 'backgroundcolor_3', 'bordercolor_3', 3, 3, 3, 3, 'fontcolor_3', 'fontfamily_3', 3, 3, 3, 'pattern_3', 3.5, 3, 'shadowcolor_3', 3, 3, 3, 3, 'text_3', 3, 3, 3, 3, 'textlabelpos_3'),
  (4, 'backgroundcolor_4', 'bordercolor_4', 4, 4, 4, 4, 'fontcolor_4', 'fontfamily_4', 4, 4, 4, 'pattern_4', 4.5, 4, 'shadowcolor_4', 4, 4, 4, 4, 'text_4', 4, 4, 4, 4, 'textlabelpos_4'),
  (5, 'backgroundcolor_5', 'bordercolor_5', 5, 5, 5, 5, 'fontcolor_5', 'fontfamily_5', 5, 5, 5, 'pattern_5', 5.5, 5, 'shadowcolor_5', 5, 5, 5, 5, 'text_5', 5, 5, 5, 5, 'textlabelpos_5');

TRUNCATE TABLE "polestar"."map_topology";
INSERT INTO "polestar"."map_topology" ("id", "acl_id", "alarmdisplaysetting", "alarm_font_size", "is_auto_link", "autosize", "backgroundcolor", "backgroundimage", "backgroundimagefilebyte", "backgroundimagefilename", "centerx", "centery", "description", "height", "linkcolorsetting", "linkwidthsetting", "name", "owneruserid", "recentalarmrefreshminute", "rotate", "scale", "is_shared", "is_show_ip", "is_show_linkeffect", "is_show_traffic", "is_system", "uuid", "width") VALUES
  (1, 1, 'alarmdisplaysetting_1', 1, 1, 1, 'backgroundcolor_1', 'backgroundimage_1', 1, 'backgroundimagefilename_1', 1.5, 1.5, 'description_1', 1, 'linkcolorsetting_1', 'linkwidthsetting_1', 'name_1', 'owneruserid_1', 1, 1.5, 1.5, 1, 1, 1, 1, 1, 'uuid_1', 1),
  (2, 2, 'alarmdisplaysetting_2', 2, 2, 2, 'backgroundcolor_2', 'backgroundimage_2', 2, 'backgroundimagefilename_2', 2.5, 2.5, 'description_2', 2, 'linkcolorsetting_2', 'linkwidthsetting_2', 'name_2', 'owneruserid_2', 2, 2.5, 2.5, 2, 2, 2, 2, 2, 'uuid_2', 2),
  (3, 3, 'alarmdisplaysetting_3', 3, 3, 3, 'backgroundcolor_3', 'backgroundimage_3', 3, 'backgroundimagefilename_3', 3.5, 3.5, 'description_3', 3, 'linkcolorsetting_3', 'linkwidthsetting_3', 'name_3', 'owneruserid_3', 3, 3.5, 3.5, 3, 3, 3, 3, 3, 'uuid_3', 3),
  (4, 4, 'alarmdisplaysetting_4', 4, 4, 4, 'backgroundcolor_4', 'backgroundimage_4', 4, 'backgroundimagefilename_4', 4.5, 4.5, 'description_4', 4, 'linkcolorsetting_4', 'linkwidthsetting_4', 'name_4', 'owneruserid_4', 4, 4.5, 4.5, 4, 4, 4, 4, 4, 'uuid_4', 4),
  (5, 5, 'alarmdisplaysetting_5', 5, 5, 5, 'backgroundcolor_5', 'backgroundimage_5', 5, 'backgroundimagefilename_5', 5.5, 5.5, 'description_5', 5, 'linkcolorsetting_5', 'linkwidthsetting_5', 'name_5', 'owneruserid_5', 5, 5.5, 5.5, 5, 5, 5, 5, 5, 'uuid_5', 5);

TRUNCATE TABLE "polestar"."map_topology_slide";
INSERT INTO "polestar"."map_topology_slide" ("map_slide_id", "map_id") VALUES
  ('map_slide_id_1', 1),
  ('map_slide_id_2', 2),
  ('map_slide_id_3', 3),
  ('map_slide_id_4', 4),
  ('map_slide_id_5', 5);

TRUNCATE TABLE "polestar"."map_topology_subnetwork";
INSERT INTO "polestar"."map_topology_subnetwork" ("id", "backgroundimage", "backgroundimagefilebyte", "backgroundimagefilename", "mapid") VALUES
  (1, 'backgroundimage_1', 1, 'backgroundimagefilename_1', 1),
  (2, 'backgroundimage_2', 2, 'backgroundimagefilename_2', 2),
  (3, 'backgroundimage_3', 3, 'backgroundimagefilename_3', 3),
  (4, 'backgroundimage_4', 4, 'backgroundimagefilename_4', 4),
  (5, 'backgroundimage_5', 5, 'backgroundimagefilename_5', 5);

TRUNCATE TABLE "polestar"."map_update_history";
INSERT INTO "polestar"."map_update_history" ("id", "ctime", "jsondata", "map_id") VALUES
  (1, 1, 'jsondata_1', 1),
  (2, 2, 'jsondata_2', 2),
  (3, 3, 'jsondata_3', 3),
  (4, 4, 'jsondata_4', 4),
  (5, 5, 'jsondata_5', 5);

TRUNCATE TABLE "polestar"."message_acronym";
INSERT INTO "polestar"."message_acronym" ("id", "acronym_desc", "acronym_name", "acronym_type", "acronym_type_name") VALUES
  (1, 'acronym_desc_1', 'acronym_name_1', 'acronym_type_1', 'acronym_type_name_1'),
  (2, 'acronym_desc_2', 'acronym_name_2', 'acronym_type_2', 'acronym_type_name_2'),
  (3, 'acronym_desc_3', 'acronym_name_3', 'acronym_type_3', 'acronym_type_name_3'),
  (4, 'acronym_desc_4', 'acronym_name_4', 'acronym_type_4', 'acronym_type_name_4'),
  (5, 'acronym_desc_5', 'acronym_name_5', 'acronym_type_5', 'acronym_type_name_5');

TRUNCATE TABLE "polestar"."metricstatisticseconddata";
INSERT INTO "polestar"."metricstatisticseconddata" ("resource_id", "definition_name", "stat_date", "interval", "avg_val", "day_of_week", "hour_of_day", "max_val", "min_val", "minute_of_hour", "second_of_minute") VALUES
  (1, 'definition_name_1', 'stat_date_1', 1, 1.5, 1, 1, 1.5, 1.5, 1, 1),
  (2, 'definition_name_2', 'stat_date_2', 2, 2.5, 2, 2, 2.5, 2.5, 2, 2),
  (3, 'definition_name_3', 'stat_date_3', 3, 3.5, 3, 3, 3.5, 3.5, 3, 3),
  (4, 'definition_name_4', 'stat_date_4', 4, 4.5, 4, 4, 4.5, 4.5, 4, 4),
  (5, 'definition_name_5', 'stat_date_5', 5, 5.5, 5, 5, 5.5, 5.5, 5, 5);

TRUNCATE TABLE "polestar"."mib_file";
INSERT INTO "polestar"."mib_file" ("id", "defaultmib", "filename", "mibfile") VALUES
  (1, 1, 'filename_1', 1),
  (2, 2, 'filename_2', 2),
  (3, 3, 'filename_3', 3),
  (4, 4, 'filename_4', 4),
  (5, 5, 'filename_5', 5);

TRUNCATE TABLE "polestar"."nms_arp";
INSERT INTO "polestar"."nms_arp" ("id", "collect_manager_id", "ip_address", "mac_address", "platform_id") VALUES
  (1, 'collect_manager_id_1', 'ip_address_1', 'mac_address_1', 1),
  (2, 'collect_manager_id_2', 'ip_address_2', 'mac_address_2', 2),
  (3, 'collect_manager_id_3', 'ip_address_3', 'mac_address_3', 3),
  (4, 'collect_manager_id_4', 'ip_address_4', 'mac_address_4', 4),
  (5, 'collect_manager_id_5', 'ip_address_5', 'mac_address_5', 5);

TRUNCATE TABLE "polestar"."nms_bridge";
INSERT INTO "polestar"."nms_bridge" ("id", "ctime", "platform_id", "port_resource_id") VALUES
  (1, 1, 1, 1),
  (2, 2, 2, 2),
  (3, 3, 3, 3),
  (4, 4, 4, 4),
  (5, 5, 5, 5);

TRUNCATE TABLE "polestar"."nms_bridge_addr_list";
INSERT INTO "polestar"."nms_bridge_addr_list" ("bridgeinfo_id", "mac_address", "ip_address", "zone_id") VALUES
  (1, 'mac_address_1', 'ip_address_1', 'zone_id_1'),
  (2, 'mac_address_2', 'ip_address_2', 'zone_id_2'),
  (3, 'mac_address_3', 'ip_address_3', 'zone_id_3'),
  (4, 'mac_address_4', 'ip_address_4', 'zone_id_4'),
  (5, 'mac_address_5', 'ip_address_5', 'zone_id_5');

TRUNCATE TABLE "polestar"."nms_default_monitor_mgmt";
INSERT INTO "polestar"."nms_default_monitor_mgmt" ("id", "apply", "description", "group_name", "name", "vendor") VALUES
  (1, 1, 'description_1', 'group_name_1', 'name_1', 'vendor_1'),
  (2, 2, 'description_2', 'group_name_2', 'name_2', 'vendor_2'),
  (3, 3, 'description_3', 'group_name_3', 'name_3', 'vendor_3'),
  (4, 4, 'description_4', 'group_name_4', 'name_4', 'vendor_4'),
  (5, 5, 'description_5', 'group_name_5', 'name_5', 'vendor_5');

TRUNCATE TABLE "polestar"."nms_dmm_model_list";
INSERT INTO "polestar"."nms_dmm_model_list" ("defaultmonitormanagement_id", "model_name") VALUES
  (1, 'model_name_1'),
  (2, 'model_name_2'),
  (3, 'model_name_3'),
  (4, 'model_name_4'),
  (5, 'model_name_5');

TRUNCATE TABLE "polestar"."nms_dmm_monitor_list";
INSERT INTO "polestar"."nms_dmm_monitor_list" ("defaultmonitormanagement_id", "custommonitors") VALUES
  (1, 'custommonitors_1'),
  (2, 'custommonitors_2'),
  (3, 'custommonitors_3'),
  (4, 'custommonitors_4'),
  (5, 'custommonitors_5');

TRUNCATE TABLE "polestar"."nms_identifier";
INSERT INTO "polestar"."nms_identifier" ("sysobjectid", "modelname", "topologyicon", "vendor") VALUES
  ('sysobjectid_1', 'modelname_1', 'topologyicon_1', 'vendor_1'),
  ('sysobjectid_2', 'modelname_2', 'topologyicon_2', 'vendor_2'),
  ('sysobjectid_3', 'modelname_3', 'topologyicon_3', 'vendor_3'),
  ('sysobjectid_4', 'modelname_4', 'topologyicon_4', 'vendor_4'),
  ('sysobjectid_5', 'modelname_5', 'topologyicon_5', 'vendor_5');

TRUNCATE TABLE "polestar"."nms_l3ip";
INSERT INTO "polestar"."nms_l3ip" ("id", "l3ips", "resourceid", "resource_id") VALUES
  (1, 'l3ips_1', 1, 1),
  (2, 'l3ips_2', 2, 2),
  (3, 'l3ips_3', 3, 3),
  (4, 'l3ips_4', 4, 4),
  (5, 'l3ips_5', 5, 5);

TRUNCATE TABLE "polestar"."nms_link_info";
INSERT INTO "polestar"."nms_link_info" ("id", "linktype", "locdeviceid", "locportdesc", "locportid", "locsysname", "remdeviceid", "remportdesc", "remportid", "remsysname") VALUES
  (1, 1, 'locdeviceid_1', 'locportdesc_1', 'locportid_1', 'locsysname_1', 'remdeviceid_1', 'remportdesc_1', 'remportid_1', 'remsysname_1'),
  (2, 2, 'locdeviceid_2', 'locportdesc_2', 'locportid_2', 'locsysname_2', 'remdeviceid_2', 'remportdesc_2', 'remportid_2', 'remsysname_2'),
  (3, 3, 'locdeviceid_3', 'locportdesc_3', 'locportid_3', 'locsysname_3', 'remdeviceid_3', 'remportdesc_3', 'remportid_3', 'remsysname_3'),
  (4, 4, 'locdeviceid_4', 'locportdesc_4', 'locportid_4', 'locsysname_4', 'remdeviceid_4', 'remportdesc_4', 'remportid_4', 'remsysname_4'),
  (5, 5, 'locdeviceid_5', 'locportdesc_5', 'locportid_5', 'locsysname_5', 'remdeviceid_5', 'remportdesc_5', 'remportid_5', 'remsysname_5');

TRUNCATE TABLE "polestar"."nms_lldp_link";
INSERT INTO "polestar"."nms_lldp_link" ("id", "locchassisid", "locportdesc", "locportid", "locsysname", "remchassisid", "remportdesc", "remportid", "remsysname") VALUES
  (1, 'locchassisid_1', 'locportdesc_1', 'locportid_1', 'locsysname_1', 'remchassisid_1', 'remportdesc_1', 'remportid_1', 'remsysname_1'),
  (2, 'locchassisid_2', 'locportdesc_2', 'locportid_2', 'locsysname_2', 'remchassisid_2', 'remportdesc_2', 'remportid_2', 'remsysname_2'),
  (3, 'locchassisid_3', 'locportdesc_3', 'locportid_3', 'locsysname_3', 'remchassisid_3', 'remportdesc_3', 'remportid_3', 'remsysname_3'),
  (4, 'locchassisid_4', 'locportdesc_4', 'locportid_4', 'locsysname_4', 'remchassisid_4', 'remportdesc_4', 'remportid_4', 'remsysname_4'),
  (5, 'locchassisid_5', 'locportdesc_5', 'locportid_5', 'locsysname_5', 'remchassisid_5', 'remportdesc_5', 'remportid_5', 'remsysname_5');

TRUNCATE TABLE "polestar"."nms_mac_addr_fdb_addr_list";
INSERT INTO "polestar"."nms_mac_addr_fdb_addr_list" ("macaddresstable_id", "fdb_addr") VALUES
  (1, 'fdb_addr_1'),
  (2, 'fdb_addr_2'),
  (3, 'fdb_addr_3'),
  (4, 'fdb_addr_4'),
  (5, 'fdb_addr_5');

TRUNCATE TABLE "polestar"."nms_mac_addr_table";
INSERT INTO "polestar"."nms_mac_addr_table" ("id", "collect_manager_id", "fdb_port_idx", "if_index", "platform_id") VALUES
  (1, 'collect_manager_id_1', 'fdb_port_idx_1', 'if_index_1', 1),
  (2, 'collect_manager_id_2', 'fdb_port_idx_2', 'if_index_2', 2),
  (3, 'collect_manager_id_3', 'fdb_port_idx_3', 'if_index_3', 3),
  (4, 'collect_manager_id_4', 'fdb_port_idx_4', 'if_index_4', 4),
  (5, 'collect_manager_id_5', 'fdb_port_idx_5', 'if_index_5', 5);

TRUNCATE TABLE "polestar"."nms_pen";
INSERT INTO "polestar"."nms_pen" ("enterprise_num", "alias", "contact_info", "email", "organization_info") VALUES
  (1, 'alias_1', 'contact_info_1', 'email_1', 'organization_info_1'),
  (2, 'alias_2', 'contact_info_2', 'email_2', 'organization_info_2'),
  (3, 'alias_3', 'contact_info_3', 'email_3', 'organization_info_3'),
  (4, 'alias_4', 'contact_info_4', 'email_4', 'organization_info_4'),
  (5, 'alias_5', 'contact_info_5', 'email_5', 'organization_info_5');

TRUNCATE TABLE "polestar"."nms_policy";
INSERT INTO "polestar"."nms_policy" ("id", "dtime", "enterprise_num", "organization", "is_use", "policy_dictionary_id") VALUES
  ('id_1', 1, 1, 'organization_1', 1, 'policy_dictionary_id_1'),
  ('id_2', 2, 2, 'organization_2', 2, 'policy_dictionary_id_2'),
  ('id_3', 3, 3, 'organization_3', 3, 'policy_dictionary_id_3'),
  ('id_4', 4, 4, 'organization_4', 4, 'policy_dictionary_id_4'),
  ('id_5', 5, 5, 'organization_5', 5, 'policy_dictionary_id_5');

TRUNCATE TABLE "polestar"."nms_policy_command";
INSERT INTO "polestar"."nms_policy_command" ("id", "command", "expect", "order_number", "is_output", "policy_id") VALUES
  ('id_1', 'command_1', 'expect_1', 1, 1, 'policy_id_1'),
  ('id_2', 'command_2', 'expect_2', 2, 2, 'policy_id_2'),
  ('id_3', 'command_3', 'expect_3', 3, 3, 'policy_id_3'),
  ('id_4', 'command_4', 'expect_4', 4, 4, 'policy_id_4'),
  ('id_5', 'command_5', 'expect_5', 5, 5, 'policy_id_5');

TRUNCATE TABLE "polestar"."nms_policy_dictionary";
INSERT INTO "polestar"."nms_policy_dictionary" ("id", "action_guide", "code", "dtime", "group_name", "inspection_standard", "name", "risk", "synopsis") VALUES
  ('id_1', 'action_guide_1', 'code_1', 1, 'group_name_1', 'inspection_standard_1', 'name_1', 1, 'synopsis_1'),
  ('id_2', 'action_guide_2', 'code_2', 2, 'group_name_2', 'inspection_standard_2', 'name_2', 2, 'synopsis_2'),
  ('id_3', 'action_guide_3', 'code_3', 3, 'group_name_3', 'inspection_standard_3', 'name_3', 3, 'synopsis_3'),
  ('id_4', 'action_guide_4', 'code_4', 4, 'group_name_4', 'inspection_standard_4', 'name_4', 4, 'synopsis_4'),
  ('id_5', 'action_guide_5', 'code_5', 5, 'group_name_5', 'inspection_standard_5', 'name_5', 5, 'synopsis_5');

TRUNCATE TABLE "polestar"."nms_policy_job";
INSERT INTO "polestar"."nms_policy_job" ("id", "ctime", "is_complete", "dtime", "description", "mtime", "modified_user", "name", "schedule_time", "policy_id") VALUES
  (1, 1, 1, 1, 'description_1', 1, 'modified_user_1', 'name_1', 1, 'policy_id_1'),
  (2, 2, 2, 2, 'description_2', 2, 'modified_user_2', 'name_2', 2, 'policy_id_2'),
  (3, 3, 3, 3, 'description_3', 3, 'modified_user_3', 'name_3', 3, 'policy_id_3'),
  (4, 4, 4, 4, 'description_4', 4, 'modified_user_4', 'name_4', 4, 'policy_id_4'),
  (5, 5, 5, 5, 'description_5', 5, 'modified_user_5', 'name_5', 5, 'policy_id_5');

TRUNCATE TABLE "polestar"."nms_policy_job_resource";
INSERT INTO "polestar"."nms_policy_job_resource" ("job_id", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."nms_policy_job_result";
INSERT INTO "polestar"."nms_policy_job_result" ("id", "etime", "result_message", "stime", "is_success", "trigger_id", "job_id") VALUES
  (1, 1, 'result_message_1', 1, 1, 1, 1),
  (2, 2, 'result_message_2', 2, 2, 2, 2),
  (3, 3, 'result_message_3', 3, 3, 3, 3),
  (4, 4, 'result_message_4', 4, 4, 4, 4),
  (5, 5, 'result_message_5', 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."nms_policy_job_result_device";
INSERT INTO "polestar"."nms_policy_job_result_device" ("id", "etime", "resource_id", "result_message", "stime", "is_success", "job_result_id") VALUES
  (1, 1, 1, 'result_message_1', 1, 1, 1),
  (2, 2, 2, 'result_message_2', 2, 2, 2),
  (3, 3, 3, 'result_message_3', 3, 3, 3),
  (4, 4, 4, 'result_message_4', 4, 4, 4),
  (5, 5, 5, 'result_message_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."nms_policy_job_trigger";
INSERT INTO "polestar"."nms_policy_job_trigger" ("id", "c_unix_time", "day_val", "day_of_week_val", "expression_summary", "hour_val", "interval_type", "min_val", "month_val", "s_time", "job_id") VALUES
  (1, 1, 1, 1, 'expression_summary_1', 1, 1, 1, 1, '2026-06-01 09:00:00', 1),
  (2, 2, 2, 2, 'expression_summary_2', 2, 2, 2, 2, '2026-06-02 09:00:00', 2),
  (3, 3, 3, 3, 'expression_summary_3', 3, 3, 3, 3, '2026-06-03 09:00:00', 3),
  (4, 4, 4, 4, 'expression_summary_4', 4, 4, 4, 4, '2026-06-04 09:00:00', 4),
  (5, 5, 5, 5, 'expression_summary_5', 5, 5, 5, 5, '2026-06-05 09:00:00', 5);

TRUNCATE TABLE "polestar"."nms_policy_rule";
INSERT INTO "polestar"."nms_policy_rule" ("id", "comparison_operator", "comparison_target", "conjunction", "order_number", "policy_id") VALUES
  ('id_1', 'comparison_operator_1', 'comparison_target_1', 'conjunction_1', 1, 'policy_id_1'),
  ('id_2', 'comparison_operator_2', 'comparison_target_2', 'conjunction_2', 2, 'policy_id_2'),
  ('id_3', 'comparison_operator_3', 'comparison_target_3', 'conjunction_3', 3, 'policy_id_3'),
  ('id_4', 'comparison_operator_4', 'comparison_target_4', 'conjunction_4', 4, 'policy_id_4'),
  ('id_5', 'comparison_operator_5', 'comparison_target_5', 'conjunction_5', 5, 'policy_id_5');

TRUNCATE TABLE "polestar"."nms_private_mib";
INSERT INTO "polestar"."nms_private_mib" ("id", "oid") VALUES
  (1, 'oid_1'),
  (2, 'oid_2'),
  (3, 'oid_3'),
  (4, 'oid_4'),
  (5, 'oid_5');

TRUNCATE TABLE "polestar"."nms_private_oid";
INSERT INTO "polestar"."nms_private_oid" ("oidkey", "name", "oid", "type", "identifier_id") VALUES
  ('oidkey_1', 'name_1', 'oid_1', 'type_1', 'identifier_id_1'),
  ('oidkey_2', 'name_2', 'oid_2', 'type_2', 'identifier_id_2'),
  ('oidkey_3', 'name_3', 'oid_3', 'type_3', 'identifier_id_3'),
  ('oidkey_4', 'name_4', 'oid_4', 'type_4', 'identifier_id_4'),
  ('oidkey_5', 'name_5', 'oid_5', 'type_5', 'identifier_id_5');

TRUNCATE TABLE "polestar"."nms_route_table";
INSERT INTO "polestar"."nms_route_table" ("id", "collect_manager_id", "net_ip", "netmask", "nexthop", "platform_resource_id", "port_resource_id") VALUES
  (1, 'collect_manager_id_1', 'net_ip_1', 'netmask_1', 'nexthop_1', 1, 1),
  (2, 'collect_manager_id_2', 'net_ip_2', 'netmask_2', 'nexthop_2', 2, 2),
  (3, 'collect_manager_id_3', 'net_ip_3', 'netmask_3', 'nexthop_3', 3, 3),
  (4, 'collect_manager_id_4', 'net_ip_4', 'netmask_4', 'nexthop_4', 4, 4),
  (5, 'collect_manager_id_5', 'net_ip_5', 'netmask_5', 'nexthop_5', 5, 5);

TRUNCATE TABLE "polestar"."nms_snmp_custom_monitor";
INSERT INTO "polestar"."nms_snmp_custom_monitor" ("resourcetype") VALUES
  ('resourcetype_1'),
  ('resourcetype_2'),
  ('resourcetype_3'),
  ('resourcetype_4'),
  ('resourcetype_5');

TRUNCATE TABLE "polestar"."nms_snmp_entry_mon";
INSERT INTO "polestar"."nms_snmp_entry_mon" ("resourcetype") VALUES
  ('resourcetype_1'),
  ('resourcetype_2'),
  ('resourcetype_3'),
  ('resourcetype_4'),
  ('resourcetype_5');

TRUNCATE TABLE "polestar"."nms_snmp_table_mon";
INSERT INTO "polestar"."nms_snmp_table_mon" ("discoverypolicy", "idcolumn", "table_oid", "resourcetype", "entry_monitor_type") VALUES
  ('discoverypolicy_1', 1, 'table_oid_1', 'resourcetype_1', 'entry_monitor_type_1'),
  ('discoverypolicy_2', 2, 'table_oid_2', 'resourcetype_2', 'entry_monitor_type_2'),
  ('discoverypolicy_3', 3, 'table_oid_3', 'resourcetype_3', 'entry_monitor_type_3'),
  ('discoverypolicy_4', 4, 'table_oid_4', 'resourcetype_4', 'entry_monitor_type_4'),
  ('discoverypolicy_5', 5, 'table_oid_5', 'resourcetype_5', 'entry_monitor_type_5');

TRUNCATE TABLE "polestar"."nms_vlan_associate_ports";
INSERT INTO "polestar"."nms_vlan_associate_ports" ("vlaninfo_id", "associateports") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."nms_vlan_info";
INSERT INTO "polestar"."nms_vlan_info" ("id", "resourceid", "vlan_id", "vlan_name", "zoneid", "resource_id") VALUES
  (1, 1, 'vlan_id_1', 'vlan_name_1', 'zoneid_1', 1),
  (2, 2, 'vlan_id_2', 'vlan_name_2', 'zoneid_2', 2),
  (3, 3, 'vlan_id_3', 'vlan_name_3', 'zoneid_3', 3),
  (4, 4, 'vlan_id_4', 'vlan_name_4', 'zoneid_4', 4),
  (5, 5, 'vlan_id_5', 'vlan_name_5', 'zoneid_5', 5);

TRUNCATE TABLE "polestar"."oid_change_info";
INSERT INTO "polestar"."oid_change_info" ("object_id", "changed_description", "changed_name") VALUES
  ('object_id_1', 'changed_description_1', 'changed_name_1'),
  ('object_id_2', 'changed_description_2', 'changed_name_2'),
  ('object_id_3', 'changed_description_3', 'changed_name_3'),
  ('object_id_4', 'changed_description_4', 'changed_name_4'),
  ('object_id_5', 'changed_description_5', 'changed_name_5');

TRUNCATE TABLE "polestar"."oid_syntax_change_map";
INSERT INTO "polestar"."oid_syntax_change_map" ("oidchangeinfo_object_id", "name_val", "number_val", "object_id") VALUES
  ('oidchangeinfo_object_id_1', 'name_val_1', 'number_val_1', 'object_id_1'),
  ('oidchangeinfo_object_id_2', 'name_val_2', 'number_val_2', 'object_id_2'),
  ('oidchangeinfo_object_id_3', 'name_val_3', 'number_val_3', 'object_id_3'),
  ('oidchangeinfo_object_id_4', 'name_val_4', 'number_val_4', 'object_id_4'),
  ('oidchangeinfo_object_id_5', 'name_val_5', 'number_val_5', 'object_id_5');

TRUNCATE TABLE "polestar"."openshift_event_sequence";
INSERT INTO "polestar"."openshift_event_sequence" ("openshifts_resource_id", "last_event_time") VALUES
  ('openshifts_resource_id_1', 1),
  ('openshifts_resource_id_2', 2),
  ('openshifts_resource_id_3', 3),
  ('openshifts_resource_id_4', 4),
  ('openshifts_resource_id_5', 5);

TRUNCATE TABLE "polestar"."openshift_resource_mapping";
INSERT INTO "polestar"."openshift_resource_mapping" ("resource_id", "target_name", "source_resource_type", "target_resource_type") VALUES
  (1, 'target_name_1', 'source_resource_type_1', 'target_resource_type_1'),
  (2, 'target_name_2', 'source_resource_type_2', 'target_resource_type_2'),
  (3, 'target_name_3', 'source_resource_type_3', 'target_resource_type_3'),
  (4, 'target_name_4', 'source_resource_type_4', 'target_resource_type_4'),
  (5, 'target_name_5', 'source_resource_type_5', 'target_resource_type_5');

TRUNCATE TABLE "polestar"."pcm_billing_history";
INSERT INTO "polestar"."pcm_billing_history" ("id", "account_id", "billing_month", "cloud_type", "end_time", "monitoring_month", "monitoring_time", "resource_id", "service_type", "start_time", "total_cost", "unit_cost", "user_name") VALUES
  (1, 'account_id_1', 1, 'cloud_type_1', 1, 1, 1, 1, 'service_type_1', 1, 1, 1, 'user_name_1'),
  (2, 'account_id_2', 2, 'cloud_type_2', 2, 2, 2, 2, 'service_type_2', 2, 2, 2, 'user_name_2'),
  (3, 'account_id_3', 3, 'cloud_type_3', 3, 3, 3, 3, 'service_type_3', 3, 3, 3, 'user_name_3'),
  (4, 'account_id_4', 4, 'cloud_type_4', 4, 4, 4, 4, 'service_type_4', 4, 4, 4, 'user_name_4'),
  (5, 'account_id_5', 5, 'cloud_type_5', 5, 5, 5, 5, 'service_type_5', 5, 5, 5, 'user_name_5');

TRUNCATE TABLE "polestar"."pcm_billing_unit_cost";
INSERT INTO "polestar"."pcm_billing_unit_cost" ("id", "cloud_type", "end_time", "service_type", "start_time", "unit_cost") VALUES
  (1, 'cloud_type_1', 1, 'service_type_1', 1, 1),
  (2, 'cloud_type_2', 2, 'service_type_2', 2, 2),
  (3, 'cloud_type_3', 3, 'service_type_3', 3, 3),
  (4, 'cloud_type_4', 4, 'service_type_4', 4, 4),
  (5, 'cloud_type_5', 5, 'service_type_5', 5, 5);

TRUNCATE TABLE "polestar"."pcm_metering_history";
INSERT INTO "polestar"."pcm_metering_history" ("id", "account_id", "cloud_type", "end_reason", "end_time", "end_user_id", "resource_id", "service_type", "start_reason", "start_time", "start_user_id", "unit_cost", "user_name") VALUES
  (1, 'account_id_1', 'cloud_type_1', 'end_reason_1', 1, 'end_user_id_1', 1, 'service_type_1', 'start_reason_1', 1, 'start_user_id_1', 1, 'user_name_1'),
  (2, 'account_id_2', 'cloud_type_2', 'end_reason_2', 2, 'end_user_id_2', 2, 'service_type_2', 'start_reason_2', 2, 'start_user_id_2', 2, 'user_name_2'),
  (3, 'account_id_3', 'cloud_type_3', 'end_reason_3', 3, 'end_user_id_3', 3, 'service_type_3', 'start_reason_3', 3, 'start_user_id_3', 3, 'user_name_3'),
  (4, 'account_id_4', 'cloud_type_4', 'end_reason_4', 4, 'end_user_id_4', 4, 'service_type_4', 'start_reason_4', 4, 'start_user_id_4', 4, 'user_name_4'),
  (5, 'account_id_5', 'cloud_type_5', 'end_reason_5', 5, 'end_user_id_5', 5, 'service_type_5', 'start_reason_5', 5, 'start_user_id_5', 5, 'user_name_5');

TRUNCATE TABLE "polestar"."pcm_service_license_info";
INSERT INTO "polestar"."pcm_service_license_info" ("resource_id", "cloud_services", "cloud_type") VALUES
  (1, 'cloud_services_1', 'cloud_type_1'),
  (2, 'cloud_services_2', 'cloud_type_2'),
  (3, 'cloud_services_3', 'cloud_type_3'),
  (4, 'cloud_services_4', 'cloud_type_4'),
  (5, 'cloud_services_5', 'cloud_type_5');

TRUNCATE TABLE "polestar"."ping_down_history";
INSERT INTO "polestar"."ping_down_history" ("id", "ctime", "err_output", "resource_id", "send_time", "std_output") VALUES
  (1, 1, 'err_output_1', 1, 1, 'std_output_1'),
  (2, 2, 'err_output_2', 2, 2, 'std_output_2'),
  (3, 3, 'err_output_3', 3, 3, 'std_output_3'),
  (4, 4, 'err_output_4', 4, 4, 'std_output_4'),
  (5, 5, 'err_output_5', 5, 5, 'std_output_5');

TRUNCATE TABLE "polestar"."ping_retry";
INSERT INTO "polestar"."ping_retry" ("id", "res_msg", "retry_num", "send_time", "timeout_time", "pingdownhistory_id") VALUES
  (1, 'res_msg_1', 1, 1, 1, 1),
  (2, 'res_msg_2', 2, 2, 2, 2),
  (3, 'res_msg_3', 3, 3, 3, 3),
  (4, 'res_msg_4', 4, 4, 4, 4),
  (5, 'res_msg_5', 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."qrtz_blob_triggers";
INSERT INTO "polestar"."qrtz_blob_triggers" ("sched_name", "trigger_name", "trigger_group", "blob_data") VALUES
  ('sched_name_1', 'trigger_name_1', 'trigger_group_1', '\x00'::bytea),
  ('sched_name_2', 'trigger_name_2', 'trigger_group_2', '\x00'::bytea),
  ('sched_name_3', 'trigger_name_3', 'trigger_group_3', '\x00'::bytea),
  ('sched_name_4', 'trigger_name_4', 'trigger_group_4', '\x00'::bytea),
  ('sched_name_5', 'trigger_name_5', 'trigger_group_5', '\x00'::bytea);

TRUNCATE TABLE "polestar"."qrtz_calendars";
INSERT INTO "polestar"."qrtz_calendars" ("sched_name", "calendar_name", "calendar") VALUES
  ('sched_name_1', 'calendar_name_1', '\x00'::bytea),
  ('sched_name_2', 'calendar_name_2', '\x00'::bytea),
  ('sched_name_3', 'calendar_name_3', '\x00'::bytea),
  ('sched_name_4', 'calendar_name_4', '\x00'::bytea),
  ('sched_name_5', 'calendar_name_5', '\x00'::bytea);

TRUNCATE TABLE "polestar"."qrtz_cron_triggers";
INSERT INTO "polestar"."qrtz_cron_triggers" ("sched_name", "trigger_name", "trigger_group", "cron_expression", "time_zone_id") VALUES
  ('sched_name_1', 'trigger_name_1', 'trigger_group_1', 'cron_expression_1', 'time_zone_id_1'),
  ('sched_name_2', 'trigger_name_2', 'trigger_group_2', 'cron_expression_2', 'time_zone_id_2'),
  ('sched_name_3', 'trigger_name_3', 'trigger_group_3', 'cron_expression_3', 'time_zone_id_3'),
  ('sched_name_4', 'trigger_name_4', 'trigger_group_4', 'cron_expression_4', 'time_zone_id_4'),
  ('sched_name_5', 'trigger_name_5', 'trigger_group_5', 'cron_expression_5', 'time_zone_id_5');

TRUNCATE TABLE "polestar"."qrtz_fired_triggers";
INSERT INTO "polestar"."qrtz_fired_triggers" ("sched_name", "entry_id", "trigger_name", "trigger_group", "instance_name", "fired_time", "sched_time", "priority", "state", "job_name", "job_group", "is_nonconcurrent", "requests_recovery") VALUES
  ('sched_name_1', 'entry_id_1', 'trigger_name_1', 'trigger_group_1', 'instance_name_1', 1, 1, 1, 'state_1', 'job_name_1', 'job_group_1', true, true),
  ('sched_name_2', 'entry_id_2', 'trigger_name_2', 'trigger_group_2', 'instance_name_2', 2, 2, 2, 'state_2', 'job_name_2', 'job_group_2', false, false),
  ('sched_name_3', 'entry_id_3', 'trigger_name_3', 'trigger_group_3', 'instance_name_3', 3, 3, 3, 'state_3', 'job_name_3', 'job_group_3', true, true),
  ('sched_name_4', 'entry_id_4', 'trigger_name_4', 'trigger_group_4', 'instance_name_4', 4, 4, 4, 'state_4', 'job_name_4', 'job_group_4', false, false),
  ('sched_name_5', 'entry_id_5', 'trigger_name_5', 'trigger_group_5', 'instance_name_5', 5, 5, 5, 'state_5', 'job_name_5', 'job_group_5', true, true);

TRUNCATE TABLE "polestar"."qrtz_job_details";
INSERT INTO "polestar"."qrtz_job_details" ("sched_name", "job_name", "job_group", "description", "job_class_name", "is_durable", "is_nonconcurrent", "is_update_data", "requests_recovery", "job_data") VALUES
  ('sched_name_1', 'job_name_1', 'job_group_1', 'description_1', 'job_class_name_1', true, true, true, true, '\x00'::bytea),
  ('sched_name_2', 'job_name_2', 'job_group_2', 'description_2', 'job_class_name_2', false, false, false, false, '\x00'::bytea),
  ('sched_name_3', 'job_name_3', 'job_group_3', 'description_3', 'job_class_name_3', true, true, true, true, '\x00'::bytea),
  ('sched_name_4', 'job_name_4', 'job_group_4', 'description_4', 'job_class_name_4', false, false, false, false, '\x00'::bytea),
  ('sched_name_5', 'job_name_5', 'job_group_5', 'description_5', 'job_class_name_5', true, true, true, true, '\x00'::bytea);

TRUNCATE TABLE "polestar"."qrtz_locks";
INSERT INTO "polestar"."qrtz_locks" ("sched_name", "lock_name") VALUES
  ('sched_name_1', 'lock_name_1'),
  ('sched_name_2', 'lock_name_2'),
  ('sched_name_3', 'lock_name_3'),
  ('sched_name_4', 'lock_name_4'),
  ('sched_name_5', 'lock_name_5');

TRUNCATE TABLE "polestar"."qrtz_paused_trigger_grps";
INSERT INTO "polestar"."qrtz_paused_trigger_grps" ("sched_name", "trigger_group") VALUES
  ('sched_name_1', 'trigger_group_1'),
  ('sched_name_2', 'trigger_group_2'),
  ('sched_name_3', 'trigger_group_3'),
  ('sched_name_4', 'trigger_group_4'),
  ('sched_name_5', 'trigger_group_5');

TRUNCATE TABLE "polestar"."qrtz_scheduler_state";
INSERT INTO "polestar"."qrtz_scheduler_state" ("sched_name", "instance_name", "last_checkin_time", "checkin_interval") VALUES
  ('sched_name_1', 'instance_name_1', 1, 1),
  ('sched_name_2', 'instance_name_2', 2, 2),
  ('sched_name_3', 'instance_name_3', 3, 3),
  ('sched_name_4', 'instance_name_4', 4, 4),
  ('sched_name_5', 'instance_name_5', 5, 5);

TRUNCATE TABLE "polestar"."qrtz_simple_triggers";
INSERT INTO "polestar"."qrtz_simple_triggers" ("sched_name", "trigger_name", "trigger_group", "repeat_count", "repeat_interval", "times_triggered") VALUES
  ('sched_name_1', 'trigger_name_1', 'trigger_group_1', 1, 1, 1),
  ('sched_name_2', 'trigger_name_2', 'trigger_group_2', 2, 2, 2),
  ('sched_name_3', 'trigger_name_3', 'trigger_group_3', 3, 3, 3),
  ('sched_name_4', 'trigger_name_4', 'trigger_group_4', 4, 4, 4),
  ('sched_name_5', 'trigger_name_5', 'trigger_group_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."qrtz_simprop_triggers";
INSERT INTO "polestar"."qrtz_simprop_triggers" ("sched_name", "trigger_name", "trigger_group", "str_prop_1", "str_prop_2", "str_prop_3", "int_prop_1", "int_prop_2", "long_prop_1", "long_prop_2", "dec_prop_1", "dec_prop_2", "bool_prop_1", "bool_prop_2") VALUES
  ('sched_name_1', 'trigger_name_1', 'trigger_group_1', 'str_prop_1_1', 'str_prop_2_1', 'str_prop_3_1', 1, 1, 1, 1, 1.5, 1.5, true, true),
  ('sched_name_2', 'trigger_name_2', 'trigger_group_2', 'str_prop_1_2', 'str_prop_2_2', 'str_prop_3_2', 2, 2, 2, 2, 2.5, 2.5, false, false),
  ('sched_name_3', 'trigger_name_3', 'trigger_group_3', 'str_prop_1_3', 'str_prop_2_3', 'str_prop_3_3', 3, 3, 3, 3, 3.5, 3.5, true, true),
  ('sched_name_4', 'trigger_name_4', 'trigger_group_4', 'str_prop_1_4', 'str_prop_2_4', 'str_prop_3_4', 4, 4, 4, 4, 4.5, 4.5, false, false),
  ('sched_name_5', 'trigger_name_5', 'trigger_group_5', 'str_prop_1_5', 'str_prop_2_5', 'str_prop_3_5', 5, 5, 5, 5, 5.5, 5.5, true, true);

TRUNCATE TABLE "polestar"."qrtz_triggers";
INSERT INTO "polestar"."qrtz_triggers" ("sched_name", "trigger_name", "trigger_group", "job_name", "job_group", "description", "next_fire_time", "prev_fire_time", "priority", "trigger_state", "trigger_type", "start_time", "end_time", "calendar_name", "misfire_instr", "job_data") VALUES
  ('sched_name_1', 'trigger_name_1', 'trigger_group_1', 'job_name_1', 'job_group_1', 'description_1', 1, 1, 1, 'trigger_state_1', 'trigger_type_1', 1, 1, 'calendar_name_1', 1, '\x00'::bytea),
  ('sched_name_2', 'trigger_name_2', 'trigger_group_2', 'job_name_2', 'job_group_2', 'description_2', 2, 2, 2, 'trigger_state_2', 'trigger_type_2', 2, 2, 'calendar_name_2', 2, '\x00'::bytea),
  ('sched_name_3', 'trigger_name_3', 'trigger_group_3', 'job_name_3', 'job_group_3', 'description_3', 3, 3, 3, 'trigger_state_3', 'trigger_type_3', 3, 3, 'calendar_name_3', 3, '\x00'::bytea),
  ('sched_name_4', 'trigger_name_4', 'trigger_group_4', 'job_name_4', 'job_group_4', 'description_4', 4, 4, 4, 'trigger_state_4', 'trigger_type_4', 4, 4, 'calendar_name_4', 4, '\x00'::bytea),
  ('sched_name_5', 'trigger_name_5', 'trigger_group_5', 'job_name_5', 'job_group_5', 'description_5', 5, 5, 5, 'trigger_state_5', 'trigger_type_5', 5, 5, 'calendar_name_5', 5, '\x00'::bytea);

TRUNCATE TABLE "polestar"."realtime_dashboard";
INSERT INTO "polestar"."realtime_dashboard" ("id", "description", "is_view", "layout", "name", "user_id") VALUES
  (1, 'description_1', 1, 1, 'name_1', 'user_id_1'),
  (2, 'description_2', 2, 2, 'name_2', 'user_id_2'),
  (3, 'description_3', 3, 3, 'name_3', 'user_id_3'),
  (4, 'description_4', 4, 4, 'name_4', 'user_id_4'),
  (5, 'description_5', 5, 5, 'name_5', 'user_id_5');

TRUNCATE TABLE "polestar"."realtime_dashboard_resources";
INSERT INTO "polestar"."realtime_dashboard_resources" ("realtimedashboardinfo_id", "resource_order", "resource_id") VALUES
  (1, 1, 1),
  (2, 2, 2),
  (3, 3, 3),
  (4, 4, 4),
  (5, 5, 5);

TRUNCATE TABLE "polestar"."rep_definition";
INSERT INTO "polestar"."rep_definition" ("id", "acl_id", "is_attention", "business_schedule_id", "is_clear", "is_critical", "dtime", "day_of_month", "day_of_week", "desc2_content", "desc2_title", "desc_content", "desc_title", "is_error", "is_fatal", "hour_of_day", "interval_type", "is_doc", "is_hwp", "is_nxl", "is_pdf", "is_ppt", "is_xls", "is_xlsx", "last_sched_time", "is_mail", "mail_contents", "mail_title", "minute_of_hour", "next_sched_time", "ownerid", "recenthour", "reportname", "reportprovider", "reporttitle", "is_running", "schedule_type", "searchfrom", "searchto", "searchtype", "topn", "is_trouble", "user_search", "uuid", "template_id") VALUES
  (1, 1, 1, 1, 1, 1, 1, 1, 1, 'desc2_content_1', 'desc2_title_1', 'desc_content_1', 'desc_title_1', 1, 1, 1, 'interval_type_1', 1, 1, 1, 1, 1, 1, 1, 1, 1, 'mail_contents_1', 'mail_title_1', 1, 1, 'ownerid_1', 1, 'reportname_1', 'reportprovider_1', 'reporttitle_1', 1, 'schedule_type_1', 1, 1, 'searchtype_1', 1, 1, 'user_search_1', 'uuid_1', 'template_id_1'),
  (2, 2, 2, 2, 2, 2, 2, 2, 2, 'desc2_content_2', 'desc2_title_2', 'desc_content_2', 'desc_title_2', 2, 2, 2, 'interval_type_2', 2, 2, 2, 2, 2, 2, 2, 2, 2, 'mail_contents_2', 'mail_title_2', 2, 2, 'ownerid_2', 2, 'reportname_2', 'reportprovider_2', 'reporttitle_2', 2, 'schedule_type_2', 2, 2, 'searchtype_2', 2, 2, 'user_search_2', 'uuid_2', 'template_id_2'),
  (3, 3, 3, 3, 3, 3, 3, 3, 3, 'desc2_content_3', 'desc2_title_3', 'desc_content_3', 'desc_title_3', 3, 3, 3, 'interval_type_3', 3, 3, 3, 3, 3, 3, 3, 3, 3, 'mail_contents_3', 'mail_title_3', 3, 3, 'ownerid_3', 3, 'reportname_3', 'reportprovider_3', 'reporttitle_3', 3, 'schedule_type_3', 3, 3, 'searchtype_3', 3, 3, 'user_search_3', 'uuid_3', 'template_id_3'),
  (4, 4, 4, 4, 4, 4, 4, 4, 4, 'desc2_content_4', 'desc2_title_4', 'desc_content_4', 'desc_title_4', 4, 4, 4, 'interval_type_4', 4, 4, 4, 4, 4, 4, 4, 4, 4, 'mail_contents_4', 'mail_title_4', 4, 4, 'ownerid_4', 4, 'reportname_4', 'reportprovider_4', 'reporttitle_4', 4, 'schedule_type_4', 4, 4, 'searchtype_4', 4, 4, 'user_search_4', 'uuid_4', 'template_id_4'),
  (5, 5, 5, 5, 5, 5, 5, 5, 5, 'desc2_content_5', 'desc2_title_5', 'desc_content_5', 'desc_title_5', 5, 5, 5, 'interval_type_5', 5, 5, 5, 5, 5, 5, 5, 5, 5, 'mail_contents_5', 'mail_title_5', 5, 5, 'ownerid_5', 5, 'reportname_5', 'reportprovider_5', 'reporttitle_5', 5, 'schedule_type_5', 5, 5, 'searchtype_5', 5, 5, 'user_search_5', 'uuid_5', 'template_id_5');

TRUNCATE TABLE "polestar"."rep_document";
INSERT INTO "polestar"."rep_document" ("id", "doc_array", "ctime", "file_size", "reportformat", "history_id") VALUES
  (1, 1, 1, 1, 'reportformat_1', 1),
  (2, 2, 2, 2, 'reportformat_2', 2),
  (3, 3, 3, 3, 'reportformat_3', 3),
  (4, 4, 4, 4, 'reportformat_4', 4),
  (5, 5, 5, 5, 'reportformat_5', 5);

TRUNCATE TABLE "polestar"."rep_history";
INSERT INTO "polestar"."rep_history" ("id", "dtime", "end_time", "issue_time", "reason", "start_time", "is_success", "took_time", "rep_def_id") VALUES
  (1, 1, 1, 1, 'reason_1', 1, 1, 1, 1),
  (2, 2, 2, 2, 'reason_2', 2, 2, 2, 2),
  (3, 3, 3, 3, 'reason_3', 3, 3, 3, 3),
  (4, 4, 4, 4, 'reason_4', 4, 4, 4, 4),
  (5, 5, 5, 5, 'reason_5', 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."rep_inst_send";
INSERT INTO "polestar"."rep_inst_send" ("id", "is_instant", "manual_input_user", "report_def_id", "is_report_target") VALUES
  (1, 1, 'manual_input_user_1', 1, 1),
  (2, 2, 'manual_input_user_2', 2, 2),
  (3, 3, 'manual_input_user_3', 3, 3),
  (4, 4, 'manual_input_user_4', 4, 4),
  (5, 5, 'manual_input_user_5', 5, 5);

TRUNCATE TABLE "polestar"."rep_inst_user";
INSERT INTO "polestar"."rep_inst_user" ("reportinstantsend_id", "selectedusers") VALUES
  (1, 'selectedusers_1'),
  (2, 'selectedusers_2'),
  (3, 'selectedusers_3'),
  (4, 'selectedusers_4'),
  (5, 'selectedusers_5');

TRUNCATE TABLE "polestar"."rep_md_target";
INSERT INTO "polestar"."rep_md_target" ("report_def_id", "measure_def_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."rep_noti_role";
INSERT INTO "polestar"."rep_noti_role" ("reportdefinition_id", "targetroles") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."rep_noti_user";
INSERT INTO "polestar"."rep_noti_user" ("reportdefinition_id", "targetusers") VALUES
  (1, 'targetusers_1'),
  (2, 'targetusers_2'),
  (3, 'targetusers_3'),
  (4, 'targetusers_4'),
  (5, 'targetusers_5');

TRUNCATE TABLE "polestar"."rep_noti_usergroup";
INSERT INTO "polestar"."rep_noti_usergroup" ("reportdefinition_id", "targetusergroups") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."rep_resource_exclude";
INSERT INTO "polestar"."rep_resource_exclude" ("report_def_id", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."rep_resource_target";
INSERT INTO "polestar"."rep_resource_target" ("report_def_id", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."rep_template";
INSERT INTO "polestar"."rep_template" ("dtype", "templatekey", "is_alarm_grade", "is_business_schedule", "is_child_resource", "dtime", "description", "domain", "is_exclude_resource", "is_measure_def", "name", "is_period", "period_term", "rep_category", "is_resource", "is_topn", "is_user_desc", "is_user_search", "odi", "ozr") VALUES
  ('dtype_1', 'templatekey_1', 1, 1, 1, 1, 'description_1', 'domain_1', 1, 1, 'name_1', 1, 'period_term_1', 'rep_category_1', 1, 1, 1, 1, 1, 1),
  ('dtype_2', 'templatekey_2', 2, 2, 2, 2, 'description_2', 'domain_2', 2, 2, 'name_2', 2, 'period_term_2', 'rep_category_2', 2, 2, 2, 2, 2, 2),
  ('dtype_3', 'templatekey_3', 3, 3, 3, 3, 'description_3', 'domain_3', 3, 3, 'name_3', 3, 'period_term_3', 'rep_category_3', 3, 3, 3, 3, 3, 3),
  ('dtype_4', 'templatekey_4', 4, 4, 4, 4, 'description_4', 'domain_4', 4, 4, 'name_4', 4, 'period_term_4', 'rep_category_4', 4, 4, 4, 4, 4, 4),
  ('dtype_5', 'templatekey_5', 5, 5, 5, 5, 'description_5', 'domain_5', 5, 5, 'name_5', 5, 'period_term_5', 'rep_category_5', 5, 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."sap_duplication_login";
INSERT INTO "polestar"."sap_duplication_login" ("id", "login_date", "system_id", "user_id") VALUES
  (1, 'login_date_1', 'system_id_1', 'user_id_1'),
  (2, 'login_date_2', 'system_id_2', 'user_id_2'),
  (3, 'login_date_3', 'system_id_3', 'user_id_3'),
  (4, 'login_date_4', 'system_id_4', 'user_id_4'),
  (5, 'login_date_5', 'system_id_5', 'user_id_5');

TRUNCATE TABLE "polestar"."sap_profile";
INSERT INTO "polestar"."sap_profile" ("id", "create_date", "create_user", "last_change_user", "profile_desc", "profile_name", "profile_type", "system_id", "update_date") VALUES
  (1, 'create_date_1', 'create_user_1', 'last_change_user_1', 'profile_desc_1', 'profile_name_1', 'profile_type_1', 'system_id_1', 'update_date_1'),
  (2, 'create_date_2', 'create_user_2', 'last_change_user_2', 'profile_desc_2', 'profile_name_2', 'profile_type_2', 'system_id_2', 'update_date_2'),
  (3, 'create_date_3', 'create_user_3', 'last_change_user_3', 'profile_desc_3', 'profile_name_3', 'profile_type_3', 'system_id_3', 'update_date_3'),
  (4, 'create_date_4', 'create_user_4', 'last_change_user_4', 'profile_desc_4', 'profile_name_4', 'profile_type_4', 'system_id_4', 'update_date_4'),
  (5, 'create_date_5', 'create_user_5', 'last_change_user_5', 'profile_desc_5', 'profile_name_5', 'profile_type_5', 'system_id_5', 'update_date_5');

TRUNCATE TABLE "polestar"."sap_profile_relation";
INSERT INTO "polestar"."sap_profile_relation" ("id", "composite_profile", "single_profile", "system_id") VALUES
  (1, 'composite_profile_1', 'single_profile_1', 'system_id_1'),
  (2, 'composite_profile_2', 'single_profile_2', 'system_id_2'),
  (3, 'composite_profile_3', 'single_profile_3', 'system_id_3'),
  (4, 'composite_profile_4', 'single_profile_4', 'system_id_4'),
  (5, 'composite_profile_5', 'single_profile_5', 'system_id_5');

TRUNCATE TABLE "polestar"."sap_role";
INSERT INTO "polestar"."sap_role" ("id", "change_date", "change_user", "is_composite", "create_date", "create_user", "parent_role", "profile_desc", "profile_name", "role_desc", "role_name", "status_color", "status_message", "system_id") VALUES
  (1, 'change_date_1', 'change_user_1', 1, 'create_date_1', 'create_user_1', 'parent_role_1', 'profile_desc_1', 'profile_name_1', 'role_desc_1', 'role_name_1', 'status_color_1', 'status_message_1', 'system_id_1'),
  (2, 'change_date_2', 'change_user_2', 2, 'create_date_2', 'create_user_2', 'parent_role_2', 'profile_desc_2', 'profile_name_2', 'role_desc_2', 'role_name_2', 'status_color_2', 'status_message_2', 'system_id_2'),
  (3, 'change_date_3', 'change_user_3', 3, 'create_date_3', 'create_user_3', 'parent_role_3', 'profile_desc_3', 'profile_name_3', 'role_desc_3', 'role_name_3', 'status_color_3', 'status_message_3', 'system_id_3'),
  (4, 'change_date_4', 'change_user_4', 4, 'create_date_4', 'create_user_4', 'parent_role_4', 'profile_desc_4', 'profile_name_4', 'role_desc_4', 'role_name_4', 'status_color_4', 'status_message_4', 'system_id_4'),
  (5, 'change_date_5', 'change_user_5', 5, 'create_date_5', 'create_user_5', 'parent_role_5', 'profile_desc_5', 'profile_name_5', 'role_desc_5', 'role_name_5', 'status_color_5', 'status_message_5', 'system_id_5');

TRUNCATE TABLE "polestar"."sap_role_relation";
INSERT INTO "polestar"."sap_role_relation" ("id", "composite_role", "single_role", "system_id") VALUES
  (1, 'composite_role_1', 'single_role_1', 'system_id_1'),
  (2, 'composite_role_2', 'single_role_2', 'system_id_2'),
  (3, 'composite_role_3', 'single_role_3', 'system_id_3'),
  (4, 'composite_role_4', 'single_role_4', 'system_id_4'),
  (5, 'composite_role_5', 'single_role_5', 'system_id_5');

TRUNCATE TABLE "polestar"."sap_tcode";
INSERT INTO "polestar"."sap_tcode" ("id", "system_id", "tcode", "tcode_desc") VALUES
  (1, 'system_id_1', 'tcode_1', 'tcode_desc_1'),
  (2, 'system_id_2', 'tcode_2', 'tcode_desc_2'),
  (3, 'system_id_3', 'tcode_3', 'tcode_desc_3'),
  (4, 'system_id_4', 'tcode_4', 'tcode_desc_4'),
  (5, 'system_id_5', 'tcode_5', 'tcode_desc_5');

TRUNCATE TABLE "polestar"."sap_tcode_profile";
INSERT INTO "polestar"."sap_tcode_profile" ("id", "profile_name", "system_id", "tcode_high", "tcode_low") VALUES
  (1, 'profile_name_1', 'system_id_1', 'tcode_high_1', 'tcode_low_1'),
  (2, 'profile_name_2', 'system_id_2', 'tcode_high_2', 'tcode_low_2'),
  (3, 'profile_name_3', 'system_id_3', 'tcode_high_3', 'tcode_low_3'),
  (4, 'profile_name_4', 'system_id_4', 'tcode_high_4', 'tcode_low_4'),
  (5, 'profile_name_5', 'system_id_5', 'tcode_high_5', 'tcode_low_5');

TRUNCATE TABLE "polestar"."sap_tcode_role";
INSERT INTO "polestar"."sap_tcode_role" ("id", "role_name", "system_id", "tcode") VALUES
  (1, 'role_name_1', 'system_id_1', 'tcode_1'),
  (2, 'role_name_2', 'system_id_2', 'tcode_2'),
  (3, 'role_name_3', 'system_id_3', 'tcode_3'),
  (4, 'role_name_4', 'system_id_4', 'tcode_4'),
  (5, 'role_name_5', 'system_id_5', 'tcode_5');

TRUNCATE TABLE "polestar"."sap_used_tcode";
INSERT INTO "polestar"."sap_used_tcode" ("id", "system_id", "tcode", "used_date", "user_id") VALUES
  (1, 'system_id_1', 'tcode_1', 'used_date_1', 'user_id_1'),
  (2, 'system_id_2', 'tcode_2', 'used_date_2', 'user_id_2'),
  (3, 'system_id_3', 'tcode_3', 'used_date_3', 'user_id_3'),
  (4, 'system_id_4', 'tcode_4', 'used_date_4', 'user_id_4'),
  (5, 'system_id_5', 'tcode_5', 'used_date_5', 'user_id_5');

TRUNCATE TABLE "polestar"."sap_user";
INSERT INTO "polestar"."sap_user" ("id", "create_date", "creator", "last_pw_changetime", "last_logontime", "lock_status", "logon_fails", "resource_id", "room_number", "system_id", "user_class", "user_id", "user_type", "user_name") VALUES
  (1, 'create_date_1', 'creator_1', 'last_pw_changetime_1', 'last_logontime_1', 'lock_status_1', 'logon_fails_1', 1, 'room_number_1', 'system_id_1', 'user_class_1', 'user_id_1', 'user_type_1', 'user_name_1'),
  (2, 'create_date_2', 'creator_2', 'last_pw_changetime_2', 'last_logontime_2', 'lock_status_2', 'logon_fails_2', 2, 'room_number_2', 'system_id_2', 'user_class_2', 'user_id_2', 'user_type_2', 'user_name_2'),
  (3, 'create_date_3', 'creator_3', 'last_pw_changetime_3', 'last_logontime_3', 'lock_status_3', 'logon_fails_3', 3, 'room_number_3', 'system_id_3', 'user_class_3', 'user_id_3', 'user_type_3', 'user_name_3'),
  (4, 'create_date_4', 'creator_4', 'last_pw_changetime_4', 'last_logontime_4', 'lock_status_4', 'logon_fails_4', 4, 'room_number_4', 'system_id_4', 'user_class_4', 'user_id_4', 'user_type_4', 'user_name_4'),
  (5, 'create_date_5', 'creator_5', 'last_pw_changetime_5', 'last_logontime_5', 'lock_status_5', 'logon_fails_5', 5, 'room_number_5', 'system_id_5', 'user_class_5', 'user_id_5', 'user_type_5', 'user_name_5');

TRUNCATE TABLE "polestar"."sap_user_profile";
INSERT INTO "polestar"."sap_user_profile" ("id", "profile_name", "system_id", "user_id") VALUES
  (1, 'profile_name_1', 'system_id_1', 'user_id_1'),
  (2, 'profile_name_2', 'system_id_2', 'user_id_2'),
  (3, 'profile_name_3', 'system_id_3', 'user_id_3'),
  (4, 'profile_name_4', 'system_id_4', 'user_id_4'),
  (5, 'profile_name_5', 'system_id_5', 'user_id_5');

TRUNCATE TABLE "polestar"."sap_user_role";
INSERT INTO "polestar"."sap_user_role" ("id", "role_end_date", "role_name", "role_start_date", "system_id", "user_id") VALUES
  (1, 'role_end_date_1', 'role_name_1', 'role_start_date_1', 'system_id_1', 'user_id_1'),
  (2, 'role_end_date_2', 'role_name_2', 'role_start_date_2', 'system_id_2', 'user_id_2'),
  (3, 'role_end_date_3', 'role_name_3', 'role_start_date_3', 'system_id_3', 'user_id_3'),
  (4, 'role_end_date_4', 'role_name_4', 'role_start_date_4', 'system_id_4', 'user_id_4'),
  (5, 'role_end_date_5', 'role_name_5', 'role_start_date_5', 'system_id_5', 'user_id_5');

TRUNCATE TABLE "polestar"."serv_port_def";
INSERT INTO "polestar"."serv_port_def" ("id", "def_port", "serv_name") VALUES
  (1, 1, 'serv_name_1'),
  (2, 2, 'serv_name_2'),
  (3, 3, 'serv_name_3'),
  (4, 4, 'serv_name_4'),
  (5, 5, 'serv_name_5');

TRUNCATE TABLE "polestar"."server_default_port_permission";
INSERT INTO "polestar"."server_default_port_permission" ("port_number", "protocol_type", "ctime", "define_type", "dtime", "permission_yn", "port_desc") VALUES
  (1, 'protocol_type_1', 1, 'define_type_1', 1, 'permission_yn_1', 'port_desc_1'),
  (2, 'protocol_type_2', 2, 'define_type_2', 2, 'permission_yn_2', 'port_desc_2'),
  (3, 'protocol_type_3', 3, 'define_type_3', 3, 'permission_yn_3', 'port_desc_3'),
  (4, 'protocol_type_4', 4, 'define_type_4', 4, 'permission_yn_4', 'port_desc_4'),
  (5, 'protocol_type_5', 5, 'define_type_5', 5, 'permission_yn_5', 'port_desc_5');

TRUNCATE TABLE "polestar"."server_port_permission";
INSERT INTO "polestar"."server_port_permission" ("resource_id", "port_number", "protocol_type", "ctime", "define_type", "dtime", "permission_yn", "port_desc") VALUES
  (1, 1, 'protocol_type_1', 1, 'define_type_1', 1, 'permission_yn_1', 'port_desc_1'),
  (2, 2, 'protocol_type_2', 2, 'define_type_2', 2, 'permission_yn_2', 'port_desc_2'),
  (3, 3, 'protocol_type_3', 3, 'define_type_3', 3, 'permission_yn_3', 'port_desc_3'),
  (4, 4, 'protocol_type_4', 4, 'define_type_4', 4, 'permission_yn_4', 'port_desc_4'),
  (5, 5, 'protocol_type_5', 5, 'define_type_5', 5, 'permission_yn_5', 'port_desc_5');

TRUNCATE TABLE "polestar"."sms_active_agent";
INSERT INTO "polestar"."sms_active_agent" ("id", "agentid", "agentversion", "connectedap", "connectiontime", "hostname", "icon", "ipaddress", "isregisted", "ostype", "registrationtime", "serverassetno") VALUES
  (1, 'agentid_1', 'agentversion_1', 'connectedap_1', 1, 'hostname_1', 'icon_1', 'ipaddress_1', 1, 'ostype_1', 1, 'serverassetno_1'),
  (2, 'agentid_2', 'agentversion_2', 'connectedap_2', 2, 'hostname_2', 'icon_2', 'ipaddress_2', 2, 'ostype_2', 2, 'serverassetno_2'),
  (3, 'agentid_3', 'agentversion_3', 'connectedap_3', 3, 'hostname_3', 'icon_3', 'ipaddress_3', 3, 'ostype_3', 3, 'serverassetno_3'),
  (4, 'agentid_4', 'agentversion_4', 'connectedap_4', 4, 'hostname_4', 'icon_4', 'ipaddress_4', 4, 'ostype_4', 4, 'serverassetno_4'),
  (5, 'agentid_5', 'agentversion_5', 'connectedap_5', 5, 'hostname_5', 'icon_5', 'ipaddress_5', 5, 'ostype_5', 5, 'serverassetno_5');

TRUNCATE TABLE "polestar"."sms_agent_acl_list";
INSERT INTO "polestar"."sms_agent_acl_list" ("id", "aclinfo", "agentacltype", "ctime", "defaultvalue") VALUES
  (1, 'aclinfo_1', 1, 1, 1),
  (2, 'aclinfo_2', 2, 2, 2),
  (3, 'aclinfo_3', 3, 3, 3),
  (4, 'aclinfo_4', 4, 4, 4),
  (5, 'aclinfo_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."sms_agent_index_mapping";
INSERT INTO "polestar"."sms_agent_index_mapping" ("mapping_index", "resource_id") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."sms_agent_index_mapping_id";
INSERT INTO "polestar"."sms_agent_index_mapping_id" ("mapping_index", "next_index") VALUES
  ('mapping_index_1', 1),
  ('mapping_index_2', 2),
  ('mapping_index_3', 3),
  ('mapping_index_4', 4),
  ('mapping_index_5', 5);

TRUNCATE TABLE "polestar"."sms_agent_install_file_info";
INSERT INTO "polestar"."sms_agent_install_file_info" ("file_name", "description", "update_file", "file_size", "manager_name", "ostype", "upload_time", "version") VALUES
  ('file_name_1', 'description_1', 1, 1, 'manager_name_1', 'ostype_1', 1, 'version_1'),
  ('file_name_2', 'description_2', 2, 2, 'manager_name_2', 'ostype_2', 2, 'version_2'),
  ('file_name_3', 'description_3', 3, 3, 'manager_name_3', 'ostype_3', 3, 'version_3'),
  ('file_name_4', 'description_4', 4, 4, 'manager_name_4', 'ostype_4', 4, 'version_4'),
  ('file_name_5', 'description_5', 5, 5, 'manager_name_5', 'ostype_5', 5, 'version_5');

TRUNCATE TABLE "polestar"."sms_agent_patch_history";
INSERT INTO "polestar"."sms_agent_patch_history" ("id", "action_status", "agent_version", "dtime", "execute_status", "failure_reason", "patch_log", "patch_result", "resource_id", "took_time", "update_time") VALUES
  (1, 1, 'agent_version_1', 1, 'execute_status_1', 'failure_reason_1', 'patch_log_1', 'patch_result_1', 1, 1, 1),
  (2, 2, 'agent_version_2', 2, 'execute_status_2', 'failure_reason_2', 'patch_log_2', 'patch_result_2', 2, 2, 2),
  (3, 3, 'agent_version_3', 3, 'execute_status_3', 'failure_reason_3', 'patch_log_3', 'patch_result_3', 3, 3, 3),
  (4, 4, 'agent_version_4', 4, 'execute_status_4', 'failure_reason_4', 'patch_log_4', 'patch_result_4', 4, 4, 4),
  (5, 5, 'agent_version_5', 5, 'execute_status_5', 'failure_reason_5', 'patch_log_5', 'patch_result_5', 5, 5, 5);

TRUNCATE TABLE "polestar"."sms_agent_update_file_info";
INSERT INTO "polestar"."sms_agent_update_file_info" ("ostype", "description", "update_file", "file_name", "file_size", "reasontype", "upload_time", "version") VALUES
  ('ostype_1', 'description_1', 1, 'file_name_1', 1, 'reasontype_1', 1, 'version_1'),
  ('ostype_2', 'description_2', 2, 'file_name_2', 2, 'reasontype_2', 2, 'version_2'),
  ('ostype_3', 'description_3', 3, 'file_name_3', 3, 'reasontype_3', 3, 'version_3'),
  ('ostype_4', 'description_4', 4, 'file_name_4', 4, 'reasontype_4', 4, 'version_4'),
  ('ostype_5', 'description_5', 5, 'file_name_5', 5, 'reasontype_5', 5, 'version_5');

TRUNCATE TABLE "polestar"."sms_agent_update_history";
INSERT INTO "polestar"."sms_agent_update_history" ("id", "dtime", "message", "resourceid", "is_success", "took_time", "update_time") VALUES
  (1, 1, 'message_1', 1, 1, 1, 1),
  (2, 2, 'message_2', 2, 2, 2, 2),
  (3, 3, 'message_3', 3, 3, 3, 3),
  (4, 4, 'message_4', 4, 4, 4, 4),
  (5, 5, 'message_5', 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."sms_agent_version_info";
INSERT INTO "polestar"."sms_agent_version_info" ("platform_resource_id", "agent_version") VALUES
  (1, 'agent_version_1'),
  (2, 'agent_version_2'),
  (3, 'agent_version_3'),
  (4, 'agent_version_4'),
  (5, 'agent_version_5');

TRUNCATE TABLE "polestar"."sms_default_file_monitor";
INSERT INTO "polestar"."sms_default_file_monitor" ("id", "apply", "backuppath", "description", "filename", "icon", "name", "ostype", "strongtype") VALUES
  (1, 1, 'backuppath_1', 'description_1', 'filename_1', 'icon_1', 'name_1', 'ostype_1', 'strongtype_1'),
  (2, 2, 'backuppath_2', 'description_2', 'filename_2', 'icon_2', 'name_2', 'ostype_2', 'strongtype_2'),
  (3, 3, 'backuppath_3', 'description_3', 'filename_3', 'icon_3', 'name_3', 'ostype_3', 'strongtype_3'),
  (4, 4, 'backuppath_4', 'description_4', 'filename_4', 'icon_4', 'name_4', 'ostype_4', 'strongtype_4'),
  (5, 5, 'backuppath_5', 'description_5', 'filename_5', 'icon_5', 'name_5', 'ostype_5', 'strongtype_5');

TRUNCATE TABLE "polestar"."sms_default_log_monitor";
INSERT INTO "polestar"."sms_default_log_monitor" ("id", "apply", "casesensitive", "customencodingtype", "debug", "description", "encodingtype", "error", "fatal", "icon", "info", "logfile", "logtype", "matchingtype", "name", "ostype", "resource_type", "scantype", "warn") VALUES
  (1, 1, 1, 'customencodingtype_1', 'debug_1', 'description_1', 'encodingtype_1', 'error_1', 'fatal_1', 'icon_1', 'info_1', 'logfile_1', 'logtype_1', 'matchingtype_1', 'name_1', 'ostype_1', 'resource_type_1', 'scantype_1', 'warn_1'),
  (2, 2, 2, 'customencodingtype_2', 'debug_2', 'description_2', 'encodingtype_2', 'error_2', 'fatal_2', 'icon_2', 'info_2', 'logfile_2', 'logtype_2', 'matchingtype_2', 'name_2', 'ostype_2', 'resource_type_2', 'scantype_2', 'warn_2'),
  (3, 3, 3, 'customencodingtype_3', 'debug_3', 'description_3', 'encodingtype_3', 'error_3', 'fatal_3', 'icon_3', 'info_3', 'logfile_3', 'logtype_3', 'matchingtype_3', 'name_3', 'ostype_3', 'resource_type_3', 'scantype_3', 'warn_3'),
  (4, 4, 4, 'customencodingtype_4', 'debug_4', 'description_4', 'encodingtype_4', 'error_4', 'fatal_4', 'icon_4', 'info_4', 'logfile_4', 'logtype_4', 'matchingtype_4', 'name_4', 'ostype_4', 'resource_type_4', 'scantype_4', 'warn_4'),
  (5, 5, 5, 'customencodingtype_5', 'debug_5', 'description_5', 'encodingtype_5', 'error_5', 'fatal_5', 'icon_5', 'info_5', 'logfile_5', 'logtype_5', 'matchingtype_5', 'name_5', 'ostype_5', 'resource_type_5', 'scantype_5', 'warn_5');

TRUNCATE TABLE "polestar"."sms_default_process_monitor";
INSERT INTO "polestar"."sms_default_process_monitor" ("id", "apply", "description", "icon", "matchingfield", "matchingtype", "name", "ostype", "owner", "processname", "runningaccount", "startcmd", "stopcmd") VALUES
  (1, 1, 'description_1', 'icon_1', 'matchingfield_1', 'matchingtype_1', 'name_1', 'ostype_1', 'owner_1', 'processname_1', 'runningaccount_1', 'startcmd_1', 'stopcmd_1'),
  (2, 2, 'description_2', 'icon_2', 'matchingfield_2', 'matchingtype_2', 'name_2', 'ostype_2', 'owner_2', 'processname_2', 'runningaccount_2', 'startcmd_2', 'stopcmd_2'),
  (3, 3, 'description_3', 'icon_3', 'matchingfield_3', 'matchingtype_3', 'name_3', 'ostype_3', 'owner_3', 'processname_3', 'runningaccount_3', 'startcmd_3', 'stopcmd_3'),
  (4, 4, 'description_4', 'icon_4', 'matchingfield_4', 'matchingtype_4', 'name_4', 'ostype_4', 'owner_4', 'processname_4', 'runningaccount_4', 'startcmd_4', 'stopcmd_4'),
  (5, 5, 'description_5', 'icon_5', 'matchingfield_5', 'matchingtype_5', 'name_5', 'ostype_5', 'owner_5', 'processname_5', 'runningaccount_5', 'startcmd_5', 'stopcmd_5');

TRUNCATE TABLE "polestar"."sms_default_win_monitor";
INSERT INTO "polestar"."sms_default_win_monitor" ("id", "apply", "description", "name", "ostype", "resource_type", "servicename") VALUES
  (1, 1, 'description_1', 'name_1', 'ostype_1', 'resource_type_1', 'servicename_1'),
  (2, 2, 'description_2', 'name_2', 'ostype_2', 'resource_type_2', 'servicename_2'),
  (3, 3, 'description_3', 'name_3', 'ostype_3', 'resource_type_3', 'servicename_3'),
  (4, 4, 'description_4', 'name_4', 'ostype_4', 'resource_type_4', 'servicename_4'),
  (5, 5, 'description_5', 'name_5', 'ostype_5', 'resource_type_5', 'servicename_5');

TRUNCATE TABLE "polestar"."sms_proc_version";
INSERT INTO "polestar"."sms_proc_version" ("ip", "process_name", "prev_version", "recent_version") VALUES
  ('ip_1', 'process_name_1', 'prev_version_1', 'recent_version_1'),
  ('ip_2', 'process_name_2', 'prev_version_2', 'recent_version_2'),
  ('ip_3', 'process_name_3', 'prev_version_3', 'recent_version_3'),
  ('ip_4', 'process_name_4', 'prev_version_4', 'recent_version_4'),
  ('ip_5', 'process_name_5', 'prev_version_5', 'recent_version_5');

TRUNCATE TABLE "polestar"."sms_script_custom_monitor";
INSERT INTO "polestar"."sms_script_custom_monitor" ("defaulttimeoutsec", "delimiter", "script_body", "scripttype", "resourcetype") VALUES
  (1, 'delimiter_1', 'script_body_1', 'scripttype_1', 'resourcetype_1'),
  (2, 'delimiter_2', 'script_body_2', 'scripttype_2', 'resourcetype_2'),
  (3, 'delimiter_3', 'script_body_3', 'scripttype_3', 'resourcetype_3'),
  (4, 'delimiter_4', 'script_body_4', 'scripttype_4', 'resourcetype_4'),
  (5, 'delimiter_5', 'script_body_5', 'scripttype_5', 'resourcetype_5');

TRUNCATE TABLE "polestar"."sms_upload_file_hist";
INSERT INTO "polestar"."sms_upload_file_hist" ("uploadfilehistory_id", "transferfiles") VALUES
  (1, 'transferfiles_1'),
  (2, 'transferfiles_2'),
  (3, 'transferfiles_3'),
  (4, 'transferfiles_4'),
  (5, 'transferfiles_5');

TRUNCATE TABLE "polestar"."sms_upload_file_info";
INSERT INTO "polestar"."sms_upload_file_info" ("file_id", "upload_file", "file_description", "file_name", "file_path", "file_size", "is_override", "uuid", "upload_file_job_id") VALUES
  (1, 1, 'file_description_1', 'file_name_1', 'file_path_1', 1, 1, 'uuid_1', 1),
  (2, 2, 'file_description_2', 'file_name_2', 'file_path_2', 2, 2, 'uuid_2', 2),
  (3, 3, 'file_description_3', 'file_name_3', 'file_path_3', 3, 3, 'uuid_3', 3),
  (4, 4, 'file_description_4', 'file_name_4', 'file_path_4', 4, 4, 'uuid_4', 4),
  (5, 5, 'file_description_5', 'file_name_5', 'file_path_5', 5, 5, 'uuid_5', 5);

TRUNCATE TABLE "polestar"."sms_upload_file_job";
INSERT INTO "polestar"."sms_upload_file_job" ("id", "job_date", "job_description", "job_name", "user_id") VALUES
  (1, 1, 'job_description_1', 'job_name_1', 'user_id_1'),
  (2, 2, 'job_description_2', 'job_name_2', 'user_id_2'),
  (3, 3, 'job_description_3', 'job_name_3', 'user_id_3'),
  (4, 4, 'job_description_4', 'job_name_4', 'user_id_4'),
  (5, 5, 'job_description_5', 'job_name_5', 'user_id_5');

TRUNCATE TABLE "polestar"."sms_upload_history";
INSERT INTO "polestar"."sms_upload_history" ("id", "job_end_time", "job_id", "job_name", "job_start_time", "user_id") VALUES
  (1, 1, 1, 'job_name_1', 1, 'user_id_1'),
  (2, 2, 2, 'job_name_2', 2, 'user_id_2'),
  (3, 3, 3, 'job_name_3', 3, 'user_id_3'),
  (4, 4, 4, 'job_name_4', 4, 'user_id_4'),
  (5, 5, 5, 'job_name_5', 5, 'user_id_5');

TRUNCATE TABLE "polestar"."sms_upload_loc_hist";
INSERT INTO "polestar"."sms_upload_loc_hist" ("location_id", "resource_id", "resource_name", "history_id") VALUES
  (1, 1, 'resource_name_1', 1),
  (2, 2, 'resource_name_2', 2),
  (3, 3, 'resource_name_3', 3),
  (4, 4, 'resource_name_4', 4),
  (5, 5, 'resource_name_5', 5);

TRUNCATE TABLE "polestar"."sms_upload_location";
INSERT INTO "polestar"."sms_upload_location" ("location_id", "resource_id", "resource_name", "upload_file_job_id") VALUES
  (1, 1, 'resource_name_1', 1),
  (2, 2, 'resource_name_2', 2),
  (3, 3, 'resource_name_3', 3),
  (4, 4, 'resource_name_4', 4),
  (5, 5, 'resource_name_5', 5);

TRUNCATE TABLE "polestar"."sms_upload_target_hist";
INSERT INTO "polestar"."sms_upload_target_hist" ("id", "endtime", "resourceid", "starttime", "succ", "sms_upload_hist_id") VALUES
  (1, 1, 1, 1, 1, 1),
  (2, 2, 2, 2, 2, 2),
  (3, 3, 3, 3, 3, 3),
  (4, 4, 4, 4, 4, 4),
  (5, 5, 5, 5, 5, 5);

TRUNCATE TABLE "polestar"."snmp_custom_monitor";
INSERT INTO "polestar"."snmp_custom_monitor" ("resourcetype") VALUES
  ('resourcetype_1'),
  ('resourcetype_2'),
  ('resourcetype_3'),
  ('resourcetype_4'),
  ('resourcetype_5');

TRUNCATE TABLE "polestar"."snmp_entry_mon";
INSERT INTO "polestar"."snmp_entry_mon" ("resourcetype") VALUES
  ('resourcetype_1'),
  ('resourcetype_2'),
  ('resourcetype_3'),
  ('resourcetype_4'),
  ('resourcetype_5');

TRUNCATE TABLE "polestar"."snmp_table_mon";
INSERT INTO "polestar"."snmp_table_mon" ("discoverypolicy", "idcolumn", "table_oid", "resourcetype", "entry_monitor_type") VALUES
  ('discoverypolicy_1', 1, 'table_oid_1', 'resourcetype_1', 'entry_monitor_type_1'),
  ('discoverypolicy_2', 2, 'table_oid_2', 'resourcetype_2', 'entry_monitor_type_2'),
  ('discoverypolicy_3', 3, 'table_oid_3', 'resourcetype_3', 'entry_monitor_type_3'),
  ('discoverypolicy_4', 4, 'table_oid_4', 'resourcetype_4', 'entry_monitor_type_4'),
  ('discoverypolicy_5', 5, 'table_oid_5', 'resourcetype_5', 'entry_monitor_type_5');

TRUNCATE TABLE "polestar"."stat_favor";
INSERT INTO "polestar"."stat_favor" ("id", "description", "is_favorite", "period_mode", "name", "owneruserid", "is_shared", "statisticsmode", "business_time_id") VALUES
  (1, 'description_1', 1, 1, 'name_1', 'owneruserid_1', 1, 'statisticsmode_1', 1),
  (2, 'description_2', 2, 2, 'name_2', 'owneruserid_2', 2, 'statisticsmode_2', 2),
  (3, 'description_3', 3, 3, 'name_3', 'owneruserid_3', 3, 'statisticsmode_3', 3),
  (4, 'description_4', 4, 4, 'name_4', 'owneruserid_4', 4, 'statisticsmode_4', 4),
  (5, 'description_5', 5, 5, 'name_5', 'owneruserid_5', 5, 'statisticsmode_5', 5);

TRUNCATE TABLE "polestar"."stat_favor_business_time";
INSERT INTO "polestar"."stat_favor_business_time" ("id", "from_hour", "to_hour") VALUES
  (1, 1, 1),
  (2, 2, 2),
  (3, 3, 3),
  (4, 4, 4),
  (5, 5, 5);

TRUNCATE TABLE "polestar"."stat_favor_business_week";
INSERT INTO "polestar"."stat_favor_business_week" ("business_time_id", "day_of_week") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."stat_favor_cond_dids";
INSERT INTO "polestar"."stat_favor_cond_dids" ("favoritestatisticscondition_id", "measurementdefinitionids") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."stat_favor_cond_rids";
INSERT INTO "polestar"."stat_favor_cond_rids" ("favoritestatisticscondition_id", "resourceids") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."stat_favor_condition";
INSERT INTO "polestar"."stat_favor_condition" ("id", "is_bandwidth", "is_init", "is_single", "resource_type", "row_order", "temp_id", "stat_id") VALUES
  (1, 1, 1, 1, 'resource_type_1', 1, 'temp_id_1', 1),
  (2, 2, 2, 2, 'resource_type_2', 2, 'temp_id_2', 2),
  (3, 3, 3, 3, 'resource_type_3', 3, 'temp_id_3', 3),
  (4, 4, 4, 4, 'resource_type_4', 4, 'temp_id_4', 4),
  (5, 5, 5, 5, 'resource_type_5', 5, 'temp_id_5', 5);

TRUNCATE TABLE "polestar"."syslog_event_job";
INSERT INTO "polestar"."syslog_event_job" ("id", "description", "is_enabled", "expect_script_job_id", "expect_script_job_name", "included_pattern", "name", "vendor_name") VALUES
  (1, 'description_1', 1, 'expect_script_job_id_1', 'expect_script_job_name_1', 'included_pattern_1', 'name_1', 'vendor_name_1'),
  (2, 'description_2', 2, 'expect_script_job_id_2', 'expect_script_job_name_2', 'included_pattern_2', 'name_2', 'vendor_name_2'),
  (3, 'description_3', 3, 'expect_script_job_id_3', 'expect_script_job_name_3', 'included_pattern_3', 'name_3', 'vendor_name_3'),
  (4, 'description_4', 4, 'expect_script_job_id_4', 'expect_script_job_name_4', 'included_pattern_4', 'name_4', 'vendor_name_4'),
  (5, 'description_5', 5, 'expect_script_job_id_5', 'expect_script_job_name_5', 'included_pattern_5', 'name_5', 'vendor_name_5');

TRUNCATE TABLE "polestar"."trap_definition";
INSERT INTO "polestar"."trap_definition" ("trap_oid", "is_include_all", "is_include_descr") VALUES
  ('trap_oid_1', 1, 1),
  ('trap_oid_2', 2, 2),
  ('trap_oid_3', 3, 3),
  ('trap_oid_4', 4, 4),
  ('trap_oid_5', 5, 5);

TRUNCATE TABLE "polestar"."trap_event_severity_map";
INSERT INTO "polestar"."trap_event_severity_map" ("trapdefinition_trap_oid", "event_severity", "is_exact_match", "object_id", "temp_id", "trap_value") VALUES
  ('trapdefinition_trap_oid_1', 1, 1, 'object_id_1', 'temp_id_1', 'trap_value_1'),
  ('trapdefinition_trap_oid_2', 2, 2, 'object_id_2', 'temp_id_2', 'trap_value_2'),
  ('trapdefinition_trap_oid_3', 3, 3, 'object_id_3', 'temp_id_3', 'trap_value_3'),
  ('trapdefinition_trap_oid_4', 4, 4, 'object_id_4', 'temp_id_4', 'trap_value_4'),
  ('trapdefinition_trap_oid_5', 5, 5, 'object_id_5', 'temp_id_5', 'trap_value_5');

TRUNCATE TABLE "polestar"."trap_include_map";
INSERT INTO "polestar"."trap_include_map" ("trapdefinition_trap_oid", "is_include", "object_id") VALUES
  ('trapdefinition_trap_oid_1', 1, 'object_id_1'),
  ('trapdefinition_trap_oid_2', 2, 'object_id_2'),
  ('trapdefinition_trap_oid_3', 3, 'object_id_3'),
  ('trapdefinition_trap_oid_4', 4, 'object_id_4'),
  ('trapdefinition_trap_oid_5', 5, 'object_id_5');

TRUNCATE TABLE "polestar"."trap_specific";
INSERT INTO "polestar"."trap_specific" ("specificoid", "description", "severity", "specificname", "vendor") VALUES
  ('specificoid_1', 'description_1', 'severity_1', 'specificname_1', 'vendor_1'),
  ('specificoid_2', 'description_2', 'severity_2', 'specificname_2', 'vendor_2'),
  ('specificoid_3', 'description_3', 'severity_3', 'specificname_3', 'vendor_3'),
  ('specificoid_4', 'description_4', 'severity_4', 'specificname_4', 'vendor_4'),
  ('specificoid_5', 'description_5', 'severity_5', 'specificname_5', 'vendor_5');

TRUNCATE TABLE "polestar"."vmm_alarm_log";
INSERT INTO "polestar"."vmm_alarm_log" ("id", "lasttime") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."vmm_event_log";
INSERT INTO "polestar"."vmm_event_log" ("id", "lasttime") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."vmm_task_log";
INSERT INTO "polestar"."vmm_task_log" ("id", "lastcomplectdtime", "laststarttime") VALUES
  (1, 1, 1),
  (2, 2, 2),
  (3, 3, 3),
  (4, 4, 4),
  (5, 5, 5);

TRUNCATE TABLE "polestar"."was_connection";
INSERT INTO "polestar"."was_connection" ("id", "connection_type", "db_user_name", "db_user_pwd", "defaultwasconnection", "description", "db_connection_size", "db_connection_time", "jdbc_driver", "jdbc_url", "jennifer_domain_id", "jennifer_token", "jennifer_url", "jennifer_version", "name", "db_sql") VALUES
  (1, 'connection_type_1', 'db_user_name_1', 'db_user_pwd_1', 1, 'description_1', 1, 1, 'jdbc_driver_1', 'jdbc_url_1', 'jennifer_domain_id_1', 'jennifer_token_1', 'jennifer_url_1', 'jennifer_version_1', 'name_1', 'db_sql_1'),
  (2, 'connection_type_2', 'db_user_name_2', 'db_user_pwd_2', 2, 'description_2', 2, 2, 'jdbc_driver_2', 'jdbc_url_2', 'jennifer_domain_id_2', 'jennifer_token_2', 'jennifer_url_2', 'jennifer_version_2', 'name_2', 'db_sql_2'),
  (3, 'connection_type_3', 'db_user_name_3', 'db_user_pwd_3', 3, 'description_3', 3, 3, 'jdbc_driver_3', 'jdbc_url_3', 'jennifer_domain_id_3', 'jennifer_token_3', 'jennifer_url_3', 'jennifer_version_3', 'name_3', 'db_sql_3'),
  (4, 'connection_type_4', 'db_user_name_4', 'db_user_pwd_4', 4, 'description_4', 4, 4, 'jdbc_driver_4', 'jdbc_url_4', 'jennifer_domain_id_4', 'jennifer_token_4', 'jennifer_url_4', 'jennifer_version_4', 'name_4', 'db_sql_4'),
  (5, 'connection_type_5', 'db_user_name_5', 'db_user_pwd_5', 5, 'description_5', 5, 5, 'jdbc_driver_5', 'jdbc_url_5', 'jennifer_domain_id_5', 'jennifer_token_5', 'jennifer_url_5', 'jennifer_version_5', 'name_5', 'db_sql_5');

TRUNCATE TABLE "polestar"."was_dashboard";
INSERT INTO "polestar"."was_dashboard" ("id", "instance", "resource_id", "user_id") VALUES
  (1, 'instance_1', 1, 'user_id_1'),
  (2, 'instance_2', 2, 'user_id_2'),
  (3, 'instance_3', 3, 'user_id_3'),
  (4, 'instance_4', 4, 'user_id_4'),
  (5, 'instance_5', 5, 'user_id_5');

TRUNCATE TABLE "polestar"."was_instance_abbreviation";
INSERT INTO "polestar"."was_instance_abbreviation" ("id", "name", "user_id", "resource_id") VALUES
  (1, 'name_1', 'user_id_1', 1),
  (2, 'name_2', 'user_id_2', 2),
  (3, 'name_3', 'user_id_3', 3),
  (4, 'name_4', 'user_id_4', 4),
  (5, 'name_5', 'user_id_5', 5);

TRUNCATE TABLE "polestar"."was_instance_group";
INSERT INTO "polestar"."was_instance_group" ("id", "authority", "description", "name", "user_id") VALUES
  (1, 'authority_1', 'description_1', 'name_1', 'user_id_1'),
  (2, 'authority_2', 'description_2', 'name_2', 'user_id_2'),
  (3, 'authority_3', 'description_3', 'name_3', 'user_id_3'),
  (4, 'authority_4', 'description_4', 'name_4', 'user_id_4'),
  (5, 'authority_5', 'description_5', 'name_5', 'user_id_5');

TRUNCATE TABLE "polestar"."was_instance_resource";
INSERT INTO "polestar"."was_instance_resource" ("instancegroup_id", "is_check", "resource_order", "resource_id") VALUES
  (1, 1, 1, 1),
  (2, 2, 2, 2),
  (3, 3, 3, 3),
  (4, 4, 4, 4),
  (5, 5, 5, 5);

TRUNCATE TABLE "polestar"."was_object";
INSERT INTO "polestar"."was_object" ("obj_hash", "ip", "agent_id", "hostname", "manager_id", "obj_name", "obj_type", "first_conn_time", "version", "wakeup") VALUES
  (1, 'ip_1', 'agent_id_1', 'hostname_1', 'manager_id_1', 'obj_name_1', 'obj_type_1', 1, 'version_1', 1),
  (2, 'ip_2', 'agent_id_2', 'hostname_2', 'manager_id_2', 'obj_name_2', 'obj_type_2', 2, 'version_2', 2),
  (3, 'ip_3', 'agent_id_3', 'hostname_3', 'manager_id_3', 'obj_name_3', 'obj_type_3', 3, 'version_3', 3),
  (4, 'ip_4', 'agent_id_4', 'hostname_4', 'manager_id_4', 'obj_name_4', 'obj_type_4', 4, 'version_4', 4),
  (5, 'ip_5', 'agent_id_5', 'hostname_5', 'manager_id_5', 'obj_name_5', 'obj_type_5', 5, 'version_5', 5);

TRUNCATE TABLE "polestar"."was_object_datasource";
INSERT INTO "polestar"."was_object_datasource" ("obj_hash", "obj_name", "obj_type", "first_conn_time", "instance_id") VALUES
  (1, 'obj_name_1', 'obj_type_1', 1, 1),
  (2, 'obj_name_2', 'obj_type_2', 2, 2),
  (3, 'obj_name_3', 'obj_type_3', 3, 3),
  (4, 'obj_name_4', 'obj_type_4', 4, 4),
  (5, 'obj_name_5', 'obj_type_5', 5, 5);

TRUNCATE TABLE "polestar"."was_search_condition";
INSERT INTO "polestar"."was_search_condition" ("id", "application", "clientip", "error", "lastapplied", "maxvalue", "minvalue", "name", "showmaxvalue", "showtooltip", "showtotalcount", "timeperiod", "type", "user_id", "ytickinterval") VALUES
  (1, 'application_1', 'clientip_1', 1, 1, 'maxvalue_1', 'minvalue_1', 'name_1', 1, 1, 1, 1, 'type_1', 'user_id_1', 1),
  (2, 'application_2', 'clientip_2', 2, 2, 'maxvalue_2', 'minvalue_2', 'name_2', 2, 2, 2, 2, 'type_2', 'user_id_2', 2),
  (3, 'application_3', 'clientip_3', 3, 3, 'maxvalue_3', 'minvalue_3', 'name_3', 3, 3, 3, 3, 'type_3', 'user_id_3', 3),
  (4, 'application_4', 'clientip_4', 4, 4, 'maxvalue_4', 'minvalue_4', 'name_4', 4, 4, 4, 4, 'type_4', 'user_id_4', 4),
  (5, 'application_5', 'clientip_5', 5, 5, 'maxvalue_5', 'minvalue_5', 'name_5', 5, 5, 5, 5, 'type_5', 'user_id_5', 5);

TRUNCATE TABLE "polestar"."was_search_instance";
INSERT INTO "polestar"."was_search_instance" ("instance_id", "instanceids") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."was_search_tree";
INSERT INTO "polestar"."was_search_tree" ("tree_id", "treeids") VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

TRUNCATE TABLE "polestar"."webapm_agent";
INSERT INTO "polestar"."webapm_agent" ("agent_key", "agent_version", "browser_setting", "ctime", "collect_interval", "dns_address", "hostname", "install_path", "installed_browser_info", "ipaddress", "mtime", "os_arch", "os_version", "remove_url", "resource_status", "agent_start_time", "use_putid", "use_remove_url", "was_connection_key") VALUES
  ('agent_key_1', 'agent_version_1', 'browser_setting_1', 1, 1, 'dns_address_1', 'hostname_1', 'install_path_1', 'installed_browser_info_1', 'ipaddress_1', 1, 'os_arch_1', 'os_version_1', 'remove_url_1', 'resource_status_1', 1, 1, 1, 'was_connection_key_1'),
  ('agent_key_2', 'agent_version_2', 'browser_setting_2', 2, 2, 'dns_address_2', 'hostname_2', 'install_path_2', 'installed_browser_info_2', 'ipaddress_2', 2, 'os_arch_2', 'os_version_2', 'remove_url_2', 'resource_status_2', 2, 2, 2, 'was_connection_key_2'),
  ('agent_key_3', 'agent_version_3', 'browser_setting_3', 3, 3, 'dns_address_3', 'hostname_3', 'install_path_3', 'installed_browser_info_3', 'ipaddress_3', 3, 'os_arch_3', 'os_version_3', 'remove_url_3', 'resource_status_3', 3, 3, 3, 'was_connection_key_3'),
  ('agent_key_4', 'agent_version_4', 'browser_setting_4', 4, 4, 'dns_address_4', 'hostname_4', 'install_path_4', 'installed_browser_info_4', 'ipaddress_4', 4, 'os_arch_4', 'os_version_4', 'remove_url_4', 'resource_status_4', 4, 4, 4, 'was_connection_key_4'),
  ('agent_key_5', 'agent_version_5', 'browser_setting_5', 5, 5, 'dns_address_5', 'hostname_5', 'install_path_5', 'installed_browser_info_5', 'ipaddress_5', 5, 'os_arch_5', 'os_version_5', 'remove_url_5', 'resource_status_5', 5, 5, 5, 'was_connection_key_5');

TRUNCATE TABLE "polestar"."webapm_command";
INSERT INTO "polestar"."webapm_command" ("id", "command_type", "ex_sequence", "command_index", "target", "time_stamp", "command_used", "value", "script_id") VALUES
  (1, 'command_type_1', 1, 1, 'target_1', 1, 1, 'value_1', 1),
  (2, 'command_type_2', 2, 2, 'target_2', 2, 2, 'value_2', 2),
  (3, 'command_type_3', 3, 3, 'target_3', 3, 3, 'value_3', 3),
  (4, 'command_type_4', 4, 4, 'target_4', 4, 4, 'value_4', 4),
  (5, 'command_type_5', 5, 5, 'target_5', 5, 5, 'value_5', 5);

TRUNCATE TABLE "polestar"."webapm_command_type";
INSERT INTO "polestar"."webapm_command_type" ("name", "command", "param") VALUES
  ('name_1', 'command_1', 1),
  ('name_2', 'command_2', 2),
  ('name_3', 'command_3', 3),
  ('name_4', 'command_4', 4),
  ('name_5', 'command_5', 5);

TRUNCATE TABLE "polestar"."webapm_script";
INSERT INTO "polestar"."webapm_script" ("id", "description", "name") VALUES
  (1, 'description_1', 'name_1'),
  (2, 'description_2', 'name_2'),
  (3, 'description_3', 'name_3'),
  (4, 'description_4', 'name_4'),
  (5, 'description_5', 'name_5');

TRUNCATE TABLE "polestar"."widget";
INSERT INTO "polestar"."widget" ("widget_id", "h", "icon", "min_h", "min_w", "name", "request_method", "request_url", "w", "widget_type") VALUES
  ('widget_id_1', 1, 'icon_1', 1, 1, 'name_1', 'request_method_1', 'request_url_1', 1, 'widget_type_1'),
  ('widget_id_2', 2, 'icon_2', 2, 2, 'name_2', 'request_method_2', 'request_url_2', 2, 'widget_type_2'),
  ('widget_id_3', 3, 'icon_3', 3, 3, 'name_3', 'request_method_3', 'request_url_3', 3, 'widget_type_3'),
  ('widget_id_4', 4, 'icon_4', 4, 4, 'name_4', 'request_method_4', 'request_url_4', 4, 'widget_type_4'),
  ('widget_id_5', 5, 'icon_5', 5, 5, 'name_5', 'request_method_5', 'request_url_5', 5, 'widget_type_5');

TRUNCATE TABLE "polestar"."widget_dashboard";
INSERT INTO "polestar"."widget_dashboard" ("id", "is_bookmark", "date_created", "description", "group_name", "date_modified", "name", "owner_user_id", "property") VALUES
  (1, 1, 1, 'description_1', 'group_name_1', 1, 'name_1', 'owner_user_id_1', 'property_1'),
  (2, 2, 2, 'description_2', 'group_name_2', 2, 'name_2', 'owner_user_id_2', 'property_2'),
  (3, 3, 3, 'description_3', 'group_name_3', 3, 'name_3', 'owner_user_id_3', 'property_3'),
  (4, 4, 4, 'description_4', 'group_name_4', 4, 'name_4', 'owner_user_id_4', 'property_4'),
  (5, 5, 5, 'description_5', 'group_name_5', 5, 'name_5', 'owner_user_id_5', 'property_5');

TRUNCATE TABLE "polestar"."widget_instance";
INSERT INTO "polestar"."widget_instance" ("widget_instance_id", "is_blocked", "h", "i", "min_h", "min_w", "is_moved", "property", "w", "name", "x", "y", "widget_id", "widget_dashboard_id") VALUES
  (1, 1, 1, 'i_1', 1, 1, 1, 'property_1', 1, 'name_1', 1, 1, 'widget_id_1', 1),
  (2, 2, 2, 'i_2', 2, 2, 2, 'property_2', 2, 'name_2', 2, 2, 'widget_id_2', 2),
  (3, 3, 3, 'i_3', 3, 3, 3, 'property_3', 3, 'name_3', 3, 3, 'widget_id_3', 3),
  (4, 4, 4, 'i_4', 4, 4, 4, 'property_4', 4, 'name_4', 4, 4, 'widget_id_4', 4),
  (5, 5, 5, 'i_5', 5, 5, 5, 'property_5', 5, 'name_5', 5, 5, 'widget_id_5', 5);

TRUNCATE TABLE "polestar"."widget_type";
INSERT INTO "polestar"."widget_type" ("widget_type", "description") VALUES
  ('widget_type_1', 'description_1'),
  ('widget_type_2', 'description_2'),
  ('widget_type_3', 'description_3'),
  ('widget_type_4', 'description_4'),
  ('widget_type_5', 'description_5');
