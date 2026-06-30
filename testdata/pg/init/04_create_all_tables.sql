-- 자동 생성: testdata/pg/generate_full_schema.py
-- 원본: sample/cleaned_tables.json (폴스타 DB 스키마 덤프)
-- 테이블 수: 394
-- IF NOT EXISTS 사용 — 기존 cmm_resource/core_config_prop 보존

CREATE SCHEMA IF NOT EXISTS polestar;

CREATE TABLE IF NOT EXISTS "polestar"."acc_acl" (
  "id" bigint NOT NULL,
  "auth_domain" varchar NOT NULL,
  "manageruserid" varchar,
  "ownerobjectuuid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_acl_manager" (
  "id" bigint NOT NULL,
  "mtime" timestamp,
  "ownerobjectuuid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_acl_manager_history" (
  "id" bigint NOT NULL,
  "action" varchar,
  "group_id" bigint,
  "isinherited" varchar,
  "manager_name" varchar,
  "manager_type" varchar,
  "modifiedby" varchar,
  "mtime" varchar,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_acl_perm" (
  "id" bigint NOT NULL,
  "action_part" integer NOT NULL,
  "acl_id" bigint,
  "role_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_acl_resource_manager_list" (
  "aclforresourcemanager_id" bigint NOT NULL,
  "type_id" bigint,
  "manageruserids" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_acl_resource_manager_type" (
  "id" bigint NOT NULL,
  "deletable" smallint,
  "name" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_acl_resource_managers" (
  "acl_id" bigint NOT NULL,
  "manageruserids" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_acl_resource_user_group" (
  "acl_id" bigint NOT NULL,
  "group_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_audit_log" (
  "id" bigint NOT NULL,
  "action" integer,
  "auditdetail" varchar,
  "audittime" timestamp,
  "authdomain" varchar,
  "entityid" varchar,
  "entitytype" varchar,
  "host" varchar,
  "name" varchar,
  "operation" varchar,
  "userid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_auth_domain" (
  "authdomain" varchar NOT NULL,
  "description" varchar,
  "displayname" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_login_log" (
  "id" bigint NOT NULL,
  "loginsuccess" smallint NOT NULL,
  "logintime" timestamp,
  "logouttime" timestamp,
  "managerid" varchar,
  "message" varchar,
  "userip" varchar,
  "userid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_manager_group" (
  "id" bigint NOT NULL,
  "ownerobjectuuid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_manager_group_list" (
  "aclforresourcemanagergroup_id" bigint NOT NULL,
  "managergroupids" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_role" (
  "id" bigint NOT NULL,
  "description" varchar,
  "master" smallint NOT NULL,
  "name" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_rsakey" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "privatekeyexponent" varchar,
  "privatekeymodulus" varchar,
  "uuid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_static_perm" (
  "id" bigint NOT NULL,
  "action_part" integer NOT NULL,
  "auth_domain" varchar NOT NULL,
  "role_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_user" (
  "id" varchar NOT NULL,
  "aclips" varchar,
  "alarmattentionpopup" smallint,
  "alarmattentionrepeat" smallint,
  "alarmattentionrepeattime" integer,
  "alarmattentionsound" smallint,
  "alarmbgcolor" smallint,
  "alarmclearpopup" smallint,
  "alarmclearsound" smallint,
  "alarmconsolerowsperpage" integer,
  "alarmcriticalpopup" smallint,
  "alarmcriticalrepeat" smallint,
  "alarmcriticalrepeattime" integer,
  "alarmcriticalsound" smallint,
  "alarmerrorpopup" smallint,
  "alarmerrorrepeat" smallint,
  "alarmerrorrepeattime" integer,
  "alarmerrorsound" smallint,
  "alarmfatalpopup" smallint,
  "alarmfatalrepeat" smallint,
  "alarmfatalrepeattime" integer,
  "alarmfatalsound" smallint,
  "alarmseverityfilter" varchar,
  "alarmstatusackedreptstop" smallint,
  "alarmstatusfinishedreptstop" smallint,
  "alarmstatusnotackreptstop" smallint,
  "alarmstatusprocessingreptstop" smallint,
  "alarmtroublepopup" smallint,
  "alarmtroublerepeat" smallint,
  "alarmtroublerepeattime" integer,
  "alarmtroublesound" smallint,
  "ctime" bigint,
  "company" varchar,
  "department" varchar,
  "description" varchar,
  "email" varchar,
  "enable_user_noti_status" smallint,
  "eventconsolerowsperpage" integer,
  "lastlogondate" bigint,
  "lastpasswordchanged" bigint,
  "locked" smallint,
  "logcollector" smallint,
  "maintenanceenabled" smallint,
  "messengerid" varchar,
  "notificationstatus" integer,
  "optlock" integer,
  "password" varchar NOT NULL,
  "phonenumber" varchar,
  "popupenabled" smallint,
  "receiveemail" smallint,
  "receivesms" smallint,
  "repeatenabled" smallint,
  "repeatstopenabled" smallint,
  "requiredpasswordchange" smallint,
  "soundenabled" smallint,
  "ssoexcepted" smallint,
  "userlang" varchar,
  "usertype" varchar,
  "username" varchar NOT NULL,
  "exceptexpire" smallint,
  "popuptime" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_user_group" (
  "id" bigint NOT NULL,
  "description" varchar,
  "master" smallint NOT NULL,
  "name" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_user_password_log" (
  "user_id" varchar NOT NULL,
  "change_time" bigint NOT NULL,
  "password" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_user_role" (
  "user_id" varchar NOT NULL,
  "role_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_user_url" (
  "id" bigint NOT NULL,
  "acl_id" bigint,
  "columnorder" integer,
  "description" varchar,
  "height" integer,
  "httpurl" varchar,
  "image" varchar,
  "imagefilebyte" oid,
  "imagefilename" varchar,
  "name" varchar,
  "url_type" varchar,
  "userid" varchar,
  "uuid" varchar,
  "width" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_user_working_hour" (
  "user_id" varchar NOT NULL,
  "day_of_week" integer NOT NULL,
  "working_hour" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."acc_usergroup_user" (
  "group_id" bigint NOT NULL,
  "user_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."anomaly_model_cur" (
  "anomaly_key" varchar NOT NULL,
  "distributionmodel" smallint NOT NULL,
  "end_time" bigint,
  "expired_time" bigint,
  "learning_data_size" integer,
  "period_mode" varchar,
  "model_json" text,
  "start_time" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."aws_billing_day_forecast" (
  "id" bigint NOT NULL,
  "cost" double precision,
  "forecast_time" varchar,
  "user_account_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."aws_billing_report" (
  "account_id" varchar NOT NULL,
  "access_key" varchar,
  "athena_database_name" varchar,
  "athena_table_name" varchar,
  "bucket_name" varchar,
  "index_name" varchar,
  "region" varchar,
  "report_name" varchar,
  "secret_access_key" varchar,
  "stack_name" varchar,
  "status" varchar,
  "uuid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."aws_billing_user" (
  "account_id" varchar NOT NULL,
  "account_name" varchar,
  "dtime" bigint,
  "current_month_forecast" double precision,
  "last_collection_elapsed_time" double precision,
  "last_collection_time" bigint,
  "is_managed" smallint,
  "start_date" bigint,
  "master_account_id" varchar,
  "aws_resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."aws_cloud_service" (
  "id" varchar NOT NULL,
  "description" varchar,
  "name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."aws_region" (
  "id" varchar NOT NULL,
  "description" varchar,
  "name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."azure_cloud_service" (
  "id" varchar NOT NULL,
  "description" varchar,
  "name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_base_info" (
  "resource_type_name" varchar NOT NULL,
  "business_resource_add" smallint,
  "business_tree_view" smallint,
  "target_resource_type" smallint,
  "businesslayer_type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_health" (
  "id" bigint NOT NULL,
  "alarmseverity" integer NOT NULL,
  "ctime" timestamp NOT NULL,
  "ctimestamp" bigint,
  "conditionlogtext" varchar,
  "resourcestatus" integer,
  "definition_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_health_active" (
  "biz_health_id" bigint NOT NULL,
  "alarmseverity" integer NOT NULL,
  "ctime" timestamp,
  "ctimestamp" bigint,
  "conditionlogtext" varchar,
  "definition_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_health_con_log" (
  "businesshealth_id" bigint NOT NULL,
  "ctime" timestamp,
  "conditionlogtext" varchar,
  "sourcevalue" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_health_def" (
  "dtype" varchar NOT NULL,
  "id" bigint NOT NULL,
  "businesshealthcoverage" varchar,
  "is_deleted" smallint,
  "enabled" smallint NOT NULL,
  "mtime" timestamp,
  "name" varchar,
  "alarmseverity" integer,
  "conditiontext" varchar,
  "description" varchar,
  "matchexpression" integer,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_health_def_con" (
  "dtype" varchar NOT NULL,
  "id" varchar NOT NULL,
  "time_stamp" bigint,
  "threshold_numeric" double precision,
  "threshold_numeric2" double precision,
  "operator" integer,
  "units" varchar,
  "threshold_avail" varchar,
  "calculation_method" integer,
  "threshold_string" varchar,
  "targetfunction" varchar,
  "target_platform" varchar,
  "definition_id" bigint,
  "biz_measurement_id" varchar,
  "measurement_def_id" bigint,
  "target_resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_health_def_con_avail_res" (
  "biz_health_def_con_avail_id" varchar NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_layer" (
  "type" varchar NOT NULL,
  "display_name" varchar,
  "layer_level" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_m_target" (
  "biz_m_id" varchar NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_map" (
  "id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_map_link" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "dtime" bigint,
  "dependency_key" varchar,
  "directiontype" varchar,
  "port" varchar,
  "map_id" bigint NOT NULL,
  "source_node_id" bigint NOT NULL,
  "source_resource_id" bigint,
  "target_node_id" bigint NOT NULL,
  "target_resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_measurement" (
  "id" varchar NOT NULL,
  "enabled" smallint NOT NULL,
  "targetfunction" varchar,
  "resource_id" bigint,
  "source_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_measurement_src" (
  "id" bigint NOT NULL,
  "aggregatefuntion" varchar,
  "defaultms" smallint NOT NULL,
  "description" varchar,
  "displaytype" varchar,
  "expression_str" varchar,
  "name" varchar,
  "scaletype" varchar,
  "scalevalue" integer,
  "uuid" varchar,
  "visibility" integer,
  "measurementdefinition_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."biz_resource" (
  "id" bigint NOT NULL,
  "add_type" varchar,
  "ctime" bigint,
  "chartmeasurementdefinitionid_1" bigint,
  "chartmeasurementdefinitionid_2" bigint,
  "chartresourceid_1" bigint,
  "chartresourceid_2" bigint,
  "dtime" bigint,
  "location" varchar,
  "businessservice_id" bigint,
  "map_id" bigint NOT NULL,
  "systemresource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."bsn_business_hour" (
  "schedule_id" bigint NOT NULL,
  "day_of_week" integer,
  "from_hour" integer,
  "to_hour" integer,
  "is_working_day" smallint
);

CREATE TABLE IF NOT EXISTS "polestar"."bsn_business_schedule" (
  "id" bigint NOT NULL,
  "description" varchar,
  "name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."bsn_holiday" (
  "id" bigint NOT NULL,
  "day_of_month" integer,
  "is_enabled" smallint,
  "f_date" varchar,
  "interval" integer,
  "is_lunar" smallint,
  "month_of_year" integer,
  "name" varchar,
  "uuid" varchar,
  "year_val" integer,
  "schedule_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."business_topology_map" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "iscreatedlink" smallint NOT NULL,
  "iscustom" smallint,
  "mapnamesuffix" varchar,
  "propname" varchar,
  "propresourcetype" varchar,
  "propvalue" varchar,
  "resource_id" bigint,
  "topologymap_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_account_info" (
  "account_key" varchar NOT NULL,
  "account_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_ad_community_list" (
  "community_list" bigint NOT NULL,
  "communitys" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_ad_result" (
  "id" varchar NOT NULL,
  "discovered_time" bigint,
  "first_time" bigint,
  "host_name" varchar,
  "ip_address" varchar,
  "module_type" varchar,
  "response_avg" double precision,
  "response_cnt" bigint,
  "response_max" bigint,
  "response_min" bigint,
  "response_sum" bigint,
  "response_time" bigint,
  "discover_status" varchar,
  "success_time" bigint,
  "ad_schedule_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_ad_sche_job_data" (
  "id" varchar NOT NULL,
  "is_error" smallint,
  "is_error_msg" varchar,
  "last_completion_time" bigint,
  "last_discoverd_time" bigint,
  "request_count" bigint,
  "reponse_count" bigint,
  "ad_schedule_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_ad_schedule" (
  "dtype" varchar NOT NULL,
  "id" bigint NOT NULL,
  "is_auto_regist" smallint,
  "ctime" bigint NOT NULL,
  "description" varchar,
  "discoveryinterval" integer,
  "from_ipaddress" bigint,
  "is_icmp_check" smallint,
  "mtime" bigint NOT NULL,
  "modifiedby" varchar,
  "module_type" varchar,
  "name" varchar,
  "optlock" integer,
  "is_running" smallint,
  "starttime" bigint,
  "to_ipaddress" bigint,
  "zone_id" varchar,
  "authalgorithm" varchar,
  "authpassword" varchar,
  "contextname" varchar,
  "port" integer,
  "privacyalgorithm" varchar,
  "privacypassword" varchar,
  "securelevel" varchar,
  "snmpversion" varchar,
  "usmuser" varchar,
  "group_resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm" (
  "id" bigint NOT NULL,
  "acktime" timestamp,
  "acktimestamp" bigint,
  "ackuserid" varchar,
  "ackusername" varchar,
  "alarmseverity" integer NOT NULL,
  "ctime" timestamp NOT NULL,
  "ctimestamp" bigint,
  "conditionid" varchar,
  "conditionlogtext" varchar,
  "currentalarmstatus" varchar NOT NULL,
  "currentnotemessage" varchar,
  "currentuserid" varchar,
  "currentusername" varchar,
  "dtime" timestamp,
  "dtimestamp" bigint,
  "resourcestatus" integer NOT NULL,
  "totalcount" integer,
  "definition_id" bigint NOT NULL,
  "master_definition_id" bigint,
  "prev_alarm_id" bigint,
  "resource_id" bigint NOT NULL,
  "root_alarm_id" bigint,
  "maintenancefilteringjobid" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_active" (
  "alarm_id" bigint NOT NULL,
  "accumulatedcount" integer,
  "alarmseverity" integer NOT NULL,
  "ctime" timestamp,
  "ctimestamp" bigint,
  "conditionlogtext" varchar,
  "currentalarmstatus" varchar NOT NULL,
  "deletingscheduledtime" bigint,
  "resourcestatus" integer NOT NULL,
  "definition_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL,
  "maintenancefilteringjobid" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_complex_monitor" (
  "id" bigint NOT NULL,
  "resource_id" bigint,
  "alarm_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_complex_monitor_sub" (
  "complexalarmmonitor_id" bigint NOT NULL,
  "subalarmids" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_con_log" (
  "alarm_id" bigint NOT NULL,
  "ctime" timestamp,
  "conditionlogtext" varchar,
  "sourcevalue" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_console_filter" (
  "user_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_console_filter_col" (
  "user_id" varchar NOT NULL,
  "column_name" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_def" (
  "dtype" varchar NOT NULL,
  "id" bigint NOT NULL,
  "is_deleted" smallint,
  "enabled" smallint NOT NULL,
  "mtime" timestamp,
  "name" varchar NOT NULL,
  "alarmtimeout" integer,
  "conditionlogtemplate" varchar,
  "conditiontext" varchar,
  "csvkey" varchar,
  "description" varchar,
  "notismsmessagetemplate" varchar,
  "pql" varchar,
  "resourcecoverage" varchar,
  "targetresourcetype_name" varchar,
  "timeoutseverity" integer,
  "activealarmpolicy" integer,
  "alarmseverity" integer,
  "clearmatchexpression" integer,
  "matchexpression" integer,
  "maxalarmspermin" integer,
  "resource_id" bigint,
  "coverageowner_id" bigint,
  "monitortemplate_id" varchar,
  "masterdefinition_id" bigint,
  "measurementdefinition_id" bigint,
  "isapply" smallint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_def_c_con" (
  "dtype" varchar NOT NULL,
  "id" bigint NOT NULL,
  "time_stamp" bigint,
  "threshold_numeric" double precision,
  "threshold_numeric2" double precision,
  "operator" integer,
  "units" varchar,
  "baselinecalctype" varchar,
  "baselinetype" varchar,
  "baseline_occurrences" integer,
  "thresholdvaluetype" varchar,
  "count" integer,
  "detailpattern" varchar,
  "eventseverity" integer,
  "eventsourcepattern" varchar,
  "is_include_cr" smallint,
  "is_negate_detail" smallint,
  "is_negate_source" smallint,
  "severity_operator" integer,
  "time_range" bigint,
  "frozen_eval_count" integer,
  "goal_time" bigint,
  "goaltimeunit" varchar,
  "goal_value" double precision,
  "goal_period" integer,
  "change_occurrences" integer,
  "thresholdtype" varchar,
  "oob_occurrences" integer,
  "canonicalpath" varchar,
  "pql" varchar,
  "threshold_long" bigint,
  "threshold_long_2" bigint,
  "alarmseverity" integer,
  "subalarmcount" integer,
  "timerange" integer,
  "threshold_avail" varchar,
  "dampeningtype" varchar,
  "evaluations" integer,
  "threshold_occurrences" integer,
  "threshold_string" varchar,
  "clear_definition_id" bigint,
  "definition_id" bigint,
  "measurement_def_id" bigint,
  "property_def_id" bigint,
  "subalarmdefinition_id" bigint,
  "eventtype" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_def_history" (
  "id" bigint NOT NULL,
  "alarmdefinitionmodifiedtime" bigint,
  "alarmdefinitionname" varchar,
  "ctime" bigint,
  "conditiontext" varchar,
  "crudtype" varchar,
  "is_deleted" smallint,
  "enabled" smallint NOT NULL,
  "mastermodifiedtime" bigint,
  "modifiedby" varchar,
  "is_proxy" smallint,
  "alarmdefinition_id" bigint,
  "masteralarmdefinition_id" bigint,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_def_noti" (
  "id" bigint NOT NULL,
  "alarmseverity" integer,
  "delaytimemin" integer,
  "is_noti_clear_severity" smallint,
  "notimethod" varchar NOT NULL,
  "is_noti_only_managed" smallint,
  "notitarget" varchar NOT NULL,
  "severityoperator" integer,
  "time_stamp" bigint,
  "definition_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_def_noti_group" (
  "alarmnotification_id" bigint NOT NULL,
  "targetgroups" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_def_noti_rmtype" (
  "alarmnotification_id" bigint NOT NULL,
  "targetresourcemanagertypes" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_def_noti_role" (
  "alarmnotification_id" bigint NOT NULL,
  "targetroles" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_def_noti_user" (
  "alarmnotification_id" bigint NOT NULL,
  "targetusers" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_def_s_con" (
  "id" bigint NOT NULL,
  "severity" integer NOT NULL,
  "threshold_avail" varchar,
  "dampeningtype" varchar,
  "evaluations" integer,
  "threshold_numeric" double precision,
  "threshold_numeric2" double precision,
  "occurrences" integer,
  "operator" integer NOT NULL,
  "threshold_string" varchar,
  "time_stamp" bigint,
  "units" varchar,
  "definition_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_delay_noti" (
  "id" bigint NOT NULL,
  "delaynotitime" bigint NOT NULL,
  "notiid" bigint,
  "patternnotiid" bigint,
  "repeatcount" integer,
  "is_repeatedly" smallint,
  "alarm_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_fault_type" (
  "fault_type_name" varchar NOT NULL,
  "fault_type_description" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_knowledge" (
  "id" bigint NOT NULL,
  "attachedfile" oid,
  "attachedfilename" varchar,
  "ctime" bigint,
  "faultcontent" varchar,
  "faulttypename" varchar,
  "mtime" bigint,
  "processcontent" varchar,
  "alarm_definition_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_linkage_history" (
  "id" bigint NOT NULL,
  "alarmid" bigint,
  "linkagetype" integer,
  "resultmessage" varchar,
  "rootalarmid" bigint,
  "sendtime" timestamp,
  "success" smallint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_note" (
  "id" bigint NOT NULL,
  "alarmcause" varchar,
  "alarmstatus" varchar,
  "chargeuserid" varchar,
  "chargeusername" varchar,
  "mtime" timestamp,
  "message" varchar,
  "userid" varchar,
  "username" varchar,
  "alarm_id" bigint,
  "alarm_definition_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_pattern_def_except" (
  "pattern_noti_def_id" bigint NOT NULL,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_pattern_def_target" (
  "pattern_noti_def_id" bigint NOT NULL,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_pattern_noti" (
  "id" bigint NOT NULL,
  "alarmseverity" integer,
  "delaytimemin" integer,
  "is_noti_clear_severity" smallint,
  "notimethod" varchar NOT NULL,
  "is_noti_only_managed" smallint,
  "notitarget" varchar NOT NULL,
  "severityfornoti" varchar,
  "severityoperator" integer,
  "time_stamp" bigint,
  "definition_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_pattern_noti_def" (
  "id" bigint NOT NULL,
  "included_pattern" varchar,
  "message_template" varchar,
  "is_check_both" smallint,
  "description" varchar,
  "enabled" smallint NOT NULL,
  "excluded_pattern" varchar,
  "linkaged_id" varchar,
  "mtime" timestamp,
  "name" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_pattern_noti_group" (
  "pattern_noti_id" bigint NOT NULL,
  "group_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_pattern_noti_rmtype" (
  "pattern_noti_id" bigint NOT NULL,
  "manager_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_pattern_noti_user" (
  "pattern_noti_id" bigint NOT NULL,
  "user_id" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_pattern_type_target" (
  "pattern_noti_def_id" bigint NOT NULL,
  "resource_type" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_severity_display" (
  "severity" integer NOT NULL,
  "audiofile" oid,
  "audiofilename" varchar,
  "audioupdatetime" bigint NOT NULL,
  "color" integer NOT NULL,
  "displayname" varchar NOT NULL,
  "icon" integer NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_trg_action" (
  "dtype" varchar NOT NULL,
  "id" bigint NOT NULL,
  "account" varchar,
  "actiondetail" varchar,
  "alarmseverity" integer,
  "name" varchar,
  "scriptname" varchar,
  "severityoperator" integer,
  "timeoutsec" integer NOT NULL,
  "time_stamp" bigint,
  "definition_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_alarm_trg_action_log" (
  "id" bigint NOT NULL,
  "actiondetail" varchar,
  "actionname" varchar,
  "actionstatus" varchar,
  "completiontime" bigint,
  "resultmessage" text,
  "starttime" bigint,
  "triggeractiontype" varchar,
  "alarm_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_availability" (
  "avail_pk" varchar NOT NULL,
  "disable_rate" double precision,
  "down_rate" double precision,
  "time_stamp" bigint,
  "unknown_rate" double precision,
  "up_rate" double precision
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_availability_log" (
  "id" bigint NOT NULL,
  "status" integer,
  "time_stamp" bigint,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_code" (
  "id" bigint NOT NULL,
  "code_name" varchar,
  "description" varchar,
  "message" varchar,
  "type" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_conf_columns_info" (
  "user_id" varchar NOT NULL,
  "db2_columns" varchar,
  "mariadb_columns" varchar,
  "mssql_columns" varchar,
  "mysql_columns" varchar,
  "network_columns" varchar,
  "oracle_columns" varchar,
  "postgresql_columns" varchar,
  "saphana_columns" varchar,
  "server_columns" varchar,
  "sybase_columns" varchar,
  "tibero_columns" varchar,
  "was_columns" varchar,
  "icmp_columns" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_custom_cal_m_src" (
  "id" bigint NOT NULL,
  "calculator" varchar,
  "description" varchar,
  "expression_str" varchar,
  "name" varchar,
  "units" varchar,
  "uuid" varchar,
  "visibility" integer,
  "custom_monitor_resource_type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_custom_conf_def" (
  "conf_def_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_custom_conf_src" (
  "id" bigint NOT NULL,
  "description" varchar,
  "expression_str" varchar,
  "name" varchar,
  "uuid" varchar,
  "visibility" integer,
  "custom_monitor_resource_type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_custom_mon" (
  "resourcetype" varchar NOT NULL,
  "description" varchar,
  "is_inner" smallint,
  "name" varchar,
  "version" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_custom_mon_m_src" (
  "id" bigint NOT NULL,
  "aggregatefuntion" varchar,
  "datatype" integer,
  "description" varchar,
  "expression_str" varchar,
  "name" varchar,
  "numerictype" integer,
  "savehistory" smallint,
  "scaletype" varchar,
  "scalevalue" integer,
  "units" varchar,
  "uuid" varchar,
  "visibility" integer,
  "custom_monitor_resource_type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_dependency_context" (
  "id" bigint NOT NULL,
  "conn_resource_id" bigint,
  "homo_key" varchar,
  "link_key" varchar NOT NULL,
  "link_type" integer NOT NULL,
  "relation" integer NOT NULL,
  "resource_id" bigint,
  "start_point" smallint,
  "upper" smallint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_dependency_exclude" (
  "id" bigint NOT NULL,
  "ip_address" varchar,
  "name" varchar,
  "port" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_dependency_link" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "direction" integer NOT NULL,
  "discovery_type" integer NOT NULL,
  "high_homo_key" varchar,
  "high_low_relation" smallint,
  "high_resource_id" bigint,
  "link_key" varchar NOT NULL,
  "link_name" varchar,
  "link_type" integer NOT NULL,
  "low_homo_key" varchar,
  "low_resource_id" bigint,
  "mtime" bigint,
  "relation" integer NOT NULL,
  "separation_key" varchar NOT NULL,
  "strong" smallint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_device_priority" (
  "id" bigint NOT NULL,
  "defaultpriority" smallint NOT NULL,
  "description" varchar,
  "name" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_device_priority_mm" (
  "id" bigint NOT NULL,
  "device_type" varchar,
  "managed" smallint NOT NULL,
  "name" varchar,
  "resource_type" varchar,
  "definition_id" bigint NOT NULL,
  "priority_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_device_priority_period" (
  "id" bigint NOT NULL,
  "device_type" varchar,
  "period" integer NOT NULL,
  "priority_id" bigint,
  "resource_type" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_download_file_info" (
  "id" bigint NOT NULL,
  "description" varchar,
  "update_file" oid,
  "file_name" varchar,
  "file_size" integer,
  "manager_id" varchar,
  "upload_time" timestamp NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_event_console_filter" (
  "user_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_event_console_filter_col" (
  "user_id" varchar NOT NULL,
  "column_name" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_event_def" (
  "id" bigint NOT NULL,
  "description" varchar,
  "displayname" varchar,
  "name" varchar,
  "resource_type" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_event_source" (
  "id" bigint NOT NULL,
  "issystem" smallint NOT NULL,
  "location" varchar,
  "definition_id" bigint,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_expect" (
  "id" varchar NOT NULL,
  "expect_str" varchar,
  "order_num" integer,
  "is_output" smallint,
  "is_regx" smallint,
  "send_command" varchar,
  "expectscript_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_expect_script" (
  "name" varchar NOT NULL,
  "description" varchar,
  "is_telnet_auto_login" smallint,
  "optlock" integer,
  "timeout" integer,
  "type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_expect_script_job" (
  "id" bigint NOT NULL,
  "ctime" bigint NOT NULL,
  "description" varchar,
  "expect_script_name" varchar,
  "mtime" bigint NOT NULL,
  "modifiedby" varchar,
  "name" varchar,
  "optlock" integer,
  "ostype" varchar,
  "is_running" smallint,
  "starttime" bigint,
  "vendor" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_expect_script_job_resource" (
  "job_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_expect_script_result" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "expect_script_name" varchar,
  "is_changed" smallint,
  "is_success" smallint,
  "result_message" text,
  "expect_script_job_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL,
  "occurrencejob" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_expect_script_trigger" (
  "id" bigint NOT NULL,
  "day_val" integer,
  "day_of_week_val" integer,
  "expressionsummary" varchar,
  "hour_val" integer,
  "intervaltype" integer,
  "min_val" integer,
  "month_val" integer,
  "starttime" timestamp,
  "time_stamp" bigint,
  "job_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_importance" (
  "id" bigint NOT NULL,
  "cpuinterval" integer,
  "defaultgrade" smallint NOT NULL,
  "description" varchar,
  "diskinterval" integer,
  "grade" varchar NOT NULL,
  "memoryinterval" integer,
  "networkinterval" integer,
  "processinterval" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_job_history" (
  "dtype" varchar NOT NULL,
  "id" bigint NOT NULL,
  "ctime" bigint,
  "completetime" bigint,
  "mtime" bigint,
  "message" varchar,
  "name" varchar,
  "starttime" bigint,
  "status_type" varchar,
  "target_count" integer,
  "target_names" varchar,
  "user_id" varchar,
  "user_name" varchar,
  "excluded" smallint,
  "job_id" bigint,
  "jobtype" integer,
  "pattern" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_job_history_ids" (
  "jobhistory_id" bigint NOT NULL,
  "resource_ids" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_job_history_platform" (
  "jobhistory_id" bigint NOT NULL,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_maintenance_job" (
  "id" bigint NOT NULL,
  "ctime" bigint NOT NULL,
  "completejobautorecovery" smallint,
  "completetime" bigint,
  "description" varchar,
  "excluded" smallint,
  "jobtype" varchar,
  "mtime" bigint NOT NULL,
  "modifiedby" varchar,
  "name" varchar,
  "nowstart" smallint,
  "nowstarttimemin" integer,
  "optlock" integer,
  "pattern" varchar,
  "is_running" smallint,
  "starttime" bigint,
  "maintenanceremainnotitime" integer,
  "maintenancestatusnoti" smallint,
  "notitarget" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_maintenance_job_bizskd" (
  "job_id" bigint NOT NULL,
  "schedule_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_maintenance_job_resource" (
  "job_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_maintenance_noti_target_user" (
  "maintenance_job_id" bigint NOT NULL,
  "targetusers" varchar,
  "targetresourcemanager" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_maintenance_noti_user" (
  "maintenance_job_id" bigint NOT NULL,
  "targetusers" varchar,
  "targetresourcemanager" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_maintenance_trigger" (
  "id" bigint NOT NULL,
  "is_complete_delete" smallint,
  "day_val" integer,
  "day_of_week_val" integer,
  "expressionsummary" varchar,
  "hour_val" integer,
  "intervalfixtype" integer,
  "intervaltype" integer,
  "min_val" integer,
  "month_val" integer,
  "starttime" timestamp,
  "timemin" integer NOT NULL,
  "time_stamp" bigint,
  "week_val" integer,
  "job_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_measurement" (
  "dtype" varchar NOT NULL,
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "error_message" varchar,
  "lastupdatedtime" bigint,
  "name" varchar NOT NULL,
  "time_stamp" bigint,
  "availabilitystatus" varchar,
  "numeric_value" double precision,
  "raw_value" numeric,
  "starttime" bigint,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_measurement_def" (
  "id" bigint NOT NULL,
  "is_analytics_target" smallint,
  "is_deleted" smallint,
  "description" varchar,
  "displayname" varchar,
  "displaytype" integer,
  "measurementtype" integer,
  "metrictype" integer,
  "name" varchar,
  "numerictype" integer,
  "priority" smallint,
  "protocolinfo" varchar,
  "resource_type" varchar NOT NULL,
  "save_history" smallint,
  "is_measurement_target" smallint,
  "tabulardataclass" varchar,
  "units" varchar,
  "visibility" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_measurement_def_tabular" (
  "id" bigint NOT NULL,
  "attributetype" varchar,
  "is_deleted" smallint,
  "description" varchar,
  "displayname" varchar,
  "esfieldname" varchar,
  "name" varchar,
  "summary" smallint NOT NULL,
  "units" varchar,
  "visibility" integer NOT NULL,
  "measurement_def_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_message_template" (
  "id" bigint NOT NULL,
  "description" varchar,
  "message" varchar NOT NULL,
  "priority" integer,
  "type" varchar NOT NULL,
  "type_spec" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_metric_collection" (
  "id" bigint NOT NULL,
  "cloudtype" varchar,
  "collected" smallint NOT NULL,
  "name" varchar,
  "resource_type" varchar,
  "definition_id" bigint NOT NULL,
  "metric_management_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_metric_management" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "defaultmetricmanagement" smallint NOT NULL,
  "description" varchar,
  "name" varchar NOT NULL,
  "resource_set_auto_priority" integer,
  "resource_set_auto_used" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_metric_management_con" (
  "dtype" varchar NOT NULL,
  "id" varchar NOT NULL,
  "conjunction" varchar,
  "order_number" integer,
  "time_stamp" bigint,
  "matching_value" varchar,
  "matching_value2" varchar,
  "value_string" varchar,
  "value_string2" varchar,
  "metric_management_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_metric_management_hist" (
  "id" bigint NOT NULL,
  "metric_management_id" integer NOT NULL,
  "metric_management_mtime" bigint,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_metric_stat_d" (
  "resource_id" bigint NOT NULL,
  "definition_name" varchar NOT NULL,
  "stat_date" varchar NOT NULL,
  "avg_val" double precision,
  "bottom_val" double precision,
  "day_of_week" integer,
  "hour_of_day" integer,
  "max_val" double precision,
  "min_val" double precision,
  "top_val" double precision
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_metric_stat_d_t" (
  "resource_id" bigint NOT NULL,
  "definition_name" varchar NOT NULL,
  "stat_date" varchar NOT NULL,
  "avg_val" double precision,
  "bottom_val" double precision,
  "day_of_week" integer,
  "hour_of_day" integer,
  "max_val" double precision,
  "min_val" double precision,
  "top_val" double precision
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_metric_stat_h" (
  "resource_id" bigint NOT NULL,
  "definition_name" varchar NOT NULL,
  "stat_date" varchar NOT NULL,
  "avg_val" double precision,
  "bottom_val" double precision,
  "day_of_week" integer,
  "hour_of_day" integer,
  "max_val" double precision,
  "min_val" double precision,
  "top_val" double precision
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_metric_stat_h_t" (
  "resource_id" bigint NOT NULL,
  "definition_name" varchar NOT NULL,
  "stat_date" varchar NOT NULL,
  "avg_val" double precision,
  "bottom_val" double precision,
  "day_of_week" integer,
  "hour_of_day" integer,
  "max_val" double precision,
  "min_val" double precision,
  "top_val" double precision
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_metric_stat_m" (
  "resource_id" bigint NOT NULL,
  "definition_name" varchar NOT NULL,
  "stat_date" varchar NOT NULL,
  "avg_val" double precision,
  "bottom_val" double precision,
  "day_of_week" integer,
  "hour_of_day" integer,
  "max_val" double precision,
  "min_val" double precision,
  "top_val" double precision
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_metric_stat_m_t" (
  "resource_id" bigint NOT NULL,
  "definition_name" varchar NOT NULL,
  "stat_date" varchar NOT NULL,
  "avg_val" double precision,
  "bottom_val" double precision,
  "day_of_week" integer,
  "hour_of_day" integer,
  "max_val" double precision,
  "min_val" double precision,
  "top_val" double precision
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_metric_stat_table_info" (
  "table_name" varchar NOT NULL,
  "create_date" varchar,
  "elapsed_time" bigint,
  "row_count" bigint,
  "stat_type" varchar,
  "table_type" varchar,
  "update_date" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_monitor_template" (
  "id" varchar NOT NULL,
  "acl_id" bigint,
  "ctime" bigint NOT NULL,
  "is_deleted" smallint,
  "description" varchar,
  "discoveryinterval" integer,
  "mtime" bigint NOT NULL,
  "modifiedby" varchar,
  "name" varchar,
  "optlock" integer,
  "ostype" varchar,
  "pql" varchar,
  "type" varchar,
  "uuid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_monitor_template_exception" (
  "template_id" varchar NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_monitor_template_target" (
  "template_id" varchar NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_operation_def" (
  "id" bigint NOT NULL,
  "add_alert" smallint,
  "defaulttimeout" integer NOT NULL,
  "description" varchar,
  "displayname" varchar,
  "name" varchar,
  "resource_type" varchar NOT NULL,
  "param_conf_def_id" bigint,
  "result_conf_def_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_property_def_select" (
  "property_def_id" bigint NOT NULL,
  "selectlist" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_realtime_info" (
  "id" bigint NOT NULL,
  "expirationtime" bigint NOT NULL,
  "expired" smallint NOT NULL,
  "intervalms" bigint,
  "realtimestatus" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_resource" (
  "dtype" varchar NOT NULL,
  "id" bigint NOT NULL,
  "acl_id" bigint NOT NULL,
  "acl_manager_group_id" bigint,
  "acl_manager_id" bigint,
  "avail_status" integer,
  "ctime" bigint,
  "dtime" bigint,
  "description" varchar,
  "group_path" varchar,
  "haschildren" smallint,
  "hostname" varchar,
  "id_ancestry" varchar,
  "identifier" varchar,
  "importance_id" integer NOT NULL,
  "is_inherit_avail_depend" smallint,
  "is_inherit_custom_conf" smallint,
  "is_inherit_manager_zone" smallint,
  "inheritstatus" smallint NOT NULL,
  "inventorypollinginterval" integer,
  "invisible" smallint NOT NULL,
  "ipaddress" varchar,
  "lc" integer,
  "location" varchar,
  "longpollinginterval" integer,
  "mtime" bigint,
  "manager_zone" varchar,
  "mesurementpollinginterval" integer,
  "modifiedby" varchar,
  "name" varchar NOT NULL,
  "optlock" integer,
  "order_num" integer,
  "parent_resource_id" bigint,
  "platform_resource_id" bigint,
  "pollingpolicy" integer,
  "priority" integer,
  "resourceicon" varchar,
  "resource_key" varchar NOT NULL,
  "resourcestatus" integer,
  "resource_type" varchar NOT NULL,
  "resourcetypeversion" varchar,
  "service_resource_id" bigint,
  "is_sync_desc" smallint,
  "is_sync_name" smallint,
  "uuid" varchar,
  "version" varchar,
  "system" smallint,
  "avail_depend_resource_id" bigint,
  "avail_depend_resource_id_2" bigint,
  "connection_conf_id" bigint,
  "custom_conf_id" bigint,
  "realtime_info_id" bigint,
  "resource_conf_id" bigint,
  "resource_path_id" bigint,
  "schedule_id" bigint,
  "resource_system_id" bigint,
  "business_group_resource_id" bigint,
  "group_resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_resource_lifecycle_history" (
  "id" bigint NOT NULL,
  "description" varchar,
  "event_time" bigint,
  "lifecycle_type" varchar,
  "resource_type" varchar,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_resource_maintenance" (
  "resource_id" bigint NOT NULL,
  "is_maintenance" smallint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_resource_path" (
  "id" bigint NOT NULL,
  "groupidancestry" varchar,
  "grouppathname" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_resource_schedule" (
  "resource_id" bigint NOT NULL,
  "mtime" bigint,
  "manager_id" varchar,
  "owner_manager_id" varchar,
  "scheduler_state" varchar NOT NULL,
  "seed_key" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_resource_system" (
  "id" bigint NOT NULL,
  "hostname" varchar,
  "inheritstatus" smallint NOT NULL,
  "ipaddress" varchar,
  "systemname" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_resource_type" (
  "name" varchar NOT NULL,
  "category" integer,
  "is_custom_monitor_type" smallint,
  "is_deleted" smallint,
  "description" varchar,
  "is_disabled" smallint,
  "displayname" varchar,
  "managementpolicy" integer,
  "measurementpollinginterval" integer,
  "pollingpolicy" integer,
  "resourceicon" varchar,
  "typename" varchar,
  "version" varchar,
  "conn_conf_def_id" bigint,
  "resource_conf_def_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_resource_type_parent" (
  "resourcetype_name" varchar NOT NULL,
  "parent_resource_type" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_resourcestatus_config_def" (
  "id" bigint NOT NULL,
  "default_setting" smallint,
  "name" varchar,
  "priority" integer,
  "resource_type" varchar,
  "search_option" smallint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_script_exception" (
  "job_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_script_model_list" (
  "expectscriptjob_id" bigint NOT NULL,
  "model_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_search_con_detail" (
  "dtype" varchar NOT NULL,
  "id" bigint NOT NULL,
  "is_exclude" smallint,
  "name" varchar,
  "stringvalue" varchar,
  "parent_list_id" bigint,
  "search_condition_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_search_condition" (
  "id" bigint NOT NULL,
  "description" varchar,
  "is_favorite" smallint,
  "last_applied_date" bigint,
  "name" varchar,
  "type" varchar NOT NULL,
  "userid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_service" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "checkavailability" smallint NOT NULL,
  "dtime" bigint,
  "description" varchar,
  "mtime" bigint,
  "service_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_service_associate" (
  "resource_id" bigint NOT NULL,
  "service_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202507" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202508" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202509" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202510" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202511" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202512" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202601" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202602" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202603" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202604" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202605" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_202606" (
  "resource_id" bigint NOT NULL,
  "definition_id" bigint NOT NULL,
  "time_stamp" bigint NOT NULL,
  "string_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cmm_trait_history_table_info" (
  "table_name" varchar NOT NULL,
  "create_date" varchar,
  "table_type" varchar,
  "include_view" smallint
);

CREATE TABLE IF NOT EXISTS "polestar"."command_restricted" (
  "id" bigint NOT NULL,
  "command" varchar NOT NULL,
  "command_desc" varchar,
  "is_enabled" smallint,
  "is_patterned" smallint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_bookmark" (
  "id" bigint NOT NULL,
  "domain_context" varchar,
  "bookmark_domin" varchar,
  "is_group" smallint,
  "name" varchar,
  "is_shared" smallint,
  "user_id" varchar NOT NULL,
  "parent_bookmark_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_config" (
  "id" bigint NOT NULL,
  "mtime" bigint NOT NULL,
  "conf_def_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."core_config_def" (
  "id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."core_config_history" (
  "dtype" varchar NOT NULL,
  "configuration_id" bigint NOT NULL,
  "ctime" bigint NOT NULL,
  "is_current" smallint,
  "errormessage" varchar,
  "mtime" bigint NOT NULL,
  "updatestatus" varchar,
  "userid" varchar,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_config_prop" (
  "dtype" varchar NOT NULL,
  "id" bigint NOT NULL,
  "errormessage" varchar,
  "name" varchar,
  "time_stamp" bigint,
  "stringvalue" text,
  "is_lob" smallint,
  "stringvalue_short" varchar,
  "configuration_id" bigint,
  "parent_list_id" bigint,
  "parent_map_id" bigint,
  "propertydefinition_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."core_default_job" (
  "id" bigint NOT NULL,
  "job_group" varchar NOT NULL,
  "job_name" varchar NOT NULL,
  "is_system" smallint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_event_task" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "eventtype" varchar,
  "managerid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."core_event_task_prop" (
  "id" bigint NOT NULL,
  "name" varchar,
  "stringvalue_short" varchar,
  "eventtask_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_manager_state" (
  "managerid" varchar NOT NULL,
  "activeport" integer,
  "is_client" smallint,
  "is_down" smallint,
  "ipaddress" varchar,
  "lastcheckintime" bigint NOT NULL,
  "macaddress" varchar,
  "is_master" smallint,
  "master_role" smallint,
  "is_patch" smallint,
  "port" integer NOT NULL,
  "zoneid" varchar,
  "ctime" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_noti_history_2026_15" (
  "alarmid" bigint,
  "ntime" timestamp,
  "notitype" integer,
  "resultmessage" varchar,
  "sourcename" varchar,
  "success" smallint,
  "userid" varchar,
  "username" varchar,
  "definitionid" bigint,
  "resourceid" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_noti_history_2026_16" (
  "alarmid" bigint,
  "ntime" timestamp,
  "notitype" integer,
  "resultmessage" varchar,
  "sourcename" varchar,
  "success" smallint,
  "userid" varchar,
  "username" varchar,
  "definitionid" bigint,
  "resourceid" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_noti_history_2026_17" (
  "alarmid" bigint,
  "ntime" timestamp,
  "notitype" integer,
  "resultmessage" varchar,
  "sourcename" varchar,
  "success" smallint,
  "userid" varchar,
  "username" varchar,
  "definitionid" bigint,
  "resourceid" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_noti_history_2026_18" (
  "alarmid" bigint,
  "ntime" timestamp,
  "notitype" integer,
  "resultmessage" varchar,
  "sourcename" varchar,
  "success" smallint,
  "userid" varchar,
  "username" varchar,
  "definitionid" bigint,
  "resourceid" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_noti_history_2026_19" (
  "alarmid" bigint,
  "ntime" timestamp,
  "notitype" integer,
  "resultmessage" varchar,
  "sourcename" varchar,
  "success" smallint,
  "userid" varchar,
  "username" varchar,
  "definitionid" bigint,
  "resourceid" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_noti_history_2026_20" (
  "alarmid" bigint,
  "ntime" timestamp,
  "notitype" integer,
  "resultmessage" varchar,
  "sourcename" varchar,
  "success" smallint,
  "userid" varchar,
  "username" varchar,
  "definitionid" bigint,
  "resourceid" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_noti_history_2026_21" (
  "alarmid" bigint,
  "ntime" timestamp,
  "notitype" integer,
  "resultmessage" varchar,
  "sourcename" varchar,
  "success" smallint,
  "userid" varchar,
  "username" varchar,
  "definitionid" bigint,
  "resourceid" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_noti_history_2026_22" (
  "alarmid" bigint,
  "ntime" timestamp,
  "notitype" integer,
  "resultmessage" varchar,
  "sourcename" varchar,
  "success" smallint,
  "userid" varchar,
  "username" varchar,
  "definitionid" bigint,
  "resourceid" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_noti_history_tables" (
  "table_name" varchar NOT NULL,
  "create_date" varchar,
  "table_type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."core_popup_noti" (
  "id" bigint NOT NULL,
  "alarmctime" bigint,
  "alarmid" bigint,
  "alarmname" varchar,
  "alarmseverityname" varchar,
  "message" varchar,
  "resourceid" bigint,
  "senttime" bigint,
  "source" varchar,
  "userid" varchar,
  "username" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."core_property_def" (
  "dtype" varchar NOT NULL,
  "id" bigint NOT NULL,
  "activationpolicy" integer,
  "is_deleted" smallint,
  "description" varchar,
  "displayname" varchar,
  "groupname" varchar,
  "name" varchar,
  "propertyorder" integer NOT NULL,
  "readonly" smallint NOT NULL,
  "is_searchable" smallint,
  "summary" smallint NOT NULL,
  "auto_complete_domain" varchar,
  "blockname" varchar,
  "cipher" smallint,
  "defaultvalue" varchar,
  "propertytype" integer,
  "regexpvalidator" varchar,
  "regexpvalidatormessage" varchar,
  "required" smallint,
  "selectquery" varchar,
  "validators" varchar,
  "configurationdefinition_id" bigint,
  "parent_map_id" bigint,
  "memberproperty_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."core_schema_version" (
  "installed_rank" integer NOT NULL,
  "version" varchar,
  "description" varchar NOT NULL,
  "type" varchar NOT NULL,
  "script" varchar NOT NULL,
  "checksum" integer,
  "installed_by" varchar NOT NULL,
  "installed_on" timestamp NOT NULL,
  "execution_time" integer NOT NULL,
  "success" boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."core_system_config" (
  "id" varchar NOT NULL,
  "category" varchar,
  "configurationname" varchar,
  "description" varchar,
  "displayname" varchar,
  "icon" varchar,
  "managergroup" varchar,
  "managername" varchar,
  "optlock" integer,
  "settingpolicy" integer,
  "configuration_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cust_prop_def_select" (
  "property_def_id" bigint NOT NULL,
  "selectlist" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cust_property" (
  "id" bigint NOT NULL,
  "definition_id" bigint,
  "is_error" smallint,
  "errormessage" varchar,
  "numericvalue" double precision,
  "stringvalue" varchar,
  "time_stamp" bigint,
  "is_user_edited" smallint,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cust_property_def" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "defaultvalue" varchar,
  "description" varchar,
  "editpolicy" varchar,
  "name" varchar,
  "prop_expression" varchar,
  "propertytype" varchar,
  "required" smallint NOT NULL,
  "is_summary" smallint,
  "target_resource_type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cust_table" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "description" varchar,
  "is_inconsistencty" smallint,
  "name" varchar,
  "target_resource_type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cust_table_column" (
  "id" bigint NOT NULL,
  "columnorder" integer,
  "columntype" varchar,
  "is_editable" smallint,
  "expression_str" varchar,
  "name" varchar,
  "uuid" varchar,
  "property_def_id" bigint,
  "table_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cust_table_exception" (
  "table_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cust_table_target" (
  "table_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cv_graph_template" (
  "id" bigint NOT NULL,
  "description" varchar,
  "name" varchar,
  "user_id" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."cv_line_template" (
  "id" bigint NOT NULL,
  "line_color" varchar,
  "data_type" varchar,
  "graph_type" varchar,
  "definition_id" bigint,
  "resource_type" varchar,
  "is_view" smallint,
  "graph_template_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cv_search_condition" (
  "id" bigint NOT NULL,
  "columnsize" integer,
  "description" varchar,
  "enddate" bigint,
  "fromdate" bigint,
  "graphtitle" varchar,
  "name" varchar,
  "pagesize" integer,
  "resourcestatus" integer,
  "searchperiod" integer,
  "user_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."cv_search_condition_resource" (
  "condition_id" bigint NOT NULL,
  "resources" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."cv_search_condition_template" (
  "condition_id" bigint NOT NULL,
  "graphtemplates" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."dashboard_slide" (
  "id" bigint NOT NULL,
  "description" varchar,
  "intervalminutes" integer,
  "intervalseconds" integer,
  "name" varchar,
  "is_use" smallint,
  "user_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."dashboard_slide_select" (
  "dashboardslide_id" bigint NOT NULL,
  "dashboard_id" bigint,
  "dashboard_order" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."db_attached_gadget" (
  "id" bigint NOT NULL,
  "is_collapsed" smallint,
  "color" varchar,
  "column_num" integer,
  "is_configured" smallint,
  "refreshinterval" integer,
  "row_order" integer,
  "size_x" integer,
  "size_y" integer,
  "title" varchar,
  "configuration_id" bigint,
  "dashboard_id" bigint NOT NULL,
  "gadget_key" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."db_dashboard" (
  "id" bigint NOT NULL,
  "acl_id" bigint,
  "description" varchar,
  "name" varchar,
  "owneruserid" varchar,
  "is_shared" smallint,
  "is_system" smallint,
  "uuid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."db_dashboard_favor" (
  "user_id" varchar NOT NULL,
  "dashboard_id" bigint NOT NULL,
  "priority" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."db_gadget" (
  "gadget_key" varchar NOT NULL,
  "allowpanelrefresh" smallint NOT NULL,
  "blockname" varchar,
  "category" varchar,
  "configurationeditblockname" varchar,
  "description" varchar,
  "licensedomain" varchar,
  "licenseparts" varchar,
  "headericon" varchar,
  "name" varchar,
  "pagename" varchar,
  "thumbnailimagepath" varchar,
  "conf_def_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."dpm_background_info" (
  "resource_id" bigint NOT NULL,
  "process_name" varchar NOT NULL,
  "pid" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."dpm_default_log_monitor" (
  "id" bigint NOT NULL,
  "apply" smallint NOT NULL,
  "casesensitive" smallint NOT NULL,
  "customencodingtype" varchar,
  "dbtype" varchar NOT NULL,
  "debug" varchar,
  "description" varchar,
  "encodingtype" varchar,
  "error" varchar,
  "fatal" varchar,
  "info" varchar,
  "last_updated_date" bigint,
  "logtype" varchar,
  "matchingtype" varchar,
  "name" varchar NOT NULL,
  "scantype" varchar,
  "version" varchar,
  "warn" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."dpm_enqueue" (
  "resource_id" bigint NOT NULL,
  "name" varchar NOT NULL,
  "type" varchar NOT NULL,
  "reason" varchar NOT NULL,
  "event" varchar,
  "fail_request" bigint,
  "request" bigint,
  "succ_request" bigint,
  "wait" bigint,
  "wait_time" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."dpm_latch" (
  "resource_id" bigint NOT NULL,
  "name" varchar NOT NULL,
  "gets" bigint,
  "immediate_gets" bigint,
  "immediate_misses" bigint,
  "misses" bigint,
  "sleeps" bigint,
  "wait_holding_latch" bigint,
  "wait_time" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."dpm_query_custom_monitor" (
  "is_response_time" smallint,
  "script_body" text,
  "resourcetype" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."dpm_session_stat" (
  "resource_id" bigint NOT NULL,
  "session_id" bigint NOT NULL,
  "serial" bigint NOT NULL,
  "blkchanges" bigint NOT NULL,
  "cputime" bigint NOT NULL,
  "dbtime" bigint NOT NULL,
  "executions" bigint NOT NULL,
  "hardparses" bigint NOT NULL,
  "loreads" bigint NOT NULL,
  "opencursor" bigint NOT NULL,
  "phyreads" bigint NOT NULL,
  "totalparses" bigint NOT NULL,
  "undosize" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."dpm_stat" (
  "resource_id" bigint NOT NULL,
  "stat_id" bigint NOT NULL,
  "prevvalue" bigint,
  "time" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."dpm_topsql" (
  "resource_id" bigint NOT NULL,
  "sql_id" varchar NOT NULL,
  "time" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."dpm_waitevent" (
  "resource_id" bigint NOT NULL,
  "event_id" bigint NOT NULL,
  "cputime" bigint,
  "dbtime" bigint,
  "totaltimewaited" bigint,
  "totaltimeouts" bigint,
  "totalwaits" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."es_index_info" (
  "id" bigint NOT NULL,
  "base_name" varchar,
  "class_name" varchar,
  "prev_index" varchar,
  "time_str" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."es_index_setting" (
  "id" varchar NOT NULL,
  "amount" integer,
  "description" varchar,
  "replicas" integer,
  "shards" integer,
  "indexingtype" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."es_merge_history" (
  "id" bigint NOT NULL,
  "db_query_time" bigint,
  "db_resource_size" integer,
  "endtime" timestamp,
  "es_index_time" bigint,
  "es_query_time" bigint,
  "es_request_time" bigint,
  "es_total_size" integer,
  "groupid" bigint,
  "managerid" varchar,
  "merge_rule" varchar,
  "merge_type" varchar,
  "splitmode" smallint NOT NULL,
  "splitsize" integer,
  "starttime" timestamp,
  "totaltime" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."fault_event_log" (
  "id" bigint NOT NULL,
  "lasttime" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."fileid_gen" (
  "gen_name" varchar,
  "gen_val" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."flow_app_def" (
  "id" bigint NOT NULL,
  "app_name" varchar,
  "def_ip" varchar,
  "def_iplong" bigint,
  "def_port" integer,
  "receiver_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."flow_ipgroup_def" (
  "id" bigint NOT NULL,
  "end_ip" varchar,
  "end_iplong" bigint,
  "ipgroup_name" varchar,
  "start_ip" varchar,
  "start_iplong" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."hibernate_sequences" (
  "sequence_name" varchar,
  "sequence_next_hi_value" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."id_gen" (
  "gen_name" varchar,
  "gen_val" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."ip_group" (
  "id" bigint NOT NULL,
  "is_approved" smallint,
  "description" varchar,
  "name" varchar,
  "user_id" varchar,
  "user_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."ip_group_band" (
  "id" bigint NOT NULL,
  "from_ipaddress" varchar,
  "from_ipaddress_long" bigint,
  "to_ipaddress" varchar,
  "to_ipaddress_long" bigint,
  "uuid" varchar,
  "group_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."ip_history" (
  "id" bigint NOT NULL,
  "collect_resource_id" bigint,
  "historydetail" varchar,
  "histroy_time" timestamp,
  "ip_address" varchar,
  "ip_address_long" bigint,
  "mac_address" varchar,
  "user_id" varchar,
  "user_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."ip_info" (
  "id" bigint NOT NULL,
  "is_approved" smallint,
  "ctime" bigint,
  "collect_type" varchar,
  "dtime" bigint,
  "description" varchar,
  "ip_address" varchar,
  "ip_address_long" bigint,
  "mac_address" varchar,
  "modifiedtime" bigint,
  "modified_user_id" varchar,
  "ipam_add_type" varchar,
  "is_used" varchar,
  "user_id" varchar,
  "user_name" varchar,
  "collect_resource_id" bigint,
  "connection_resource_id" bigint,
  "group_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."k8s_event_sequence" (
  "k8s_resource_id" varchar NOT NULL,
  "last_event_time" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."k8s_resource_mapping" (
  "resource_id" bigint NOT NULL,
  "target_name" varchar NOT NULL,
  "source_resource_type" varchar,
  "target_resource_type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."kb_metric_stat_avgmax" (
  "resource_id" bigint NOT NULL,
  "definition_name" varchar NOT NULL,
  "stat_date" varchar NOT NULL,
  "avg_of_max" double precision,
  "day_of_week" integer,
  "hit_count" bigint,
  "hour_of_day" integer,
  "max_val" double precision,
  "total_of_max" double precision
);

CREATE TABLE IF NOT EXISTS "polestar"."lvw_link_info" (
  "id" bigint NOT NULL,
  "link_desc" varchar,
  "link_direction" varchar NOT NULL,
  "link_key" varchar,
  "link_type" varchar NOT NULL,
  "master_host_name" varchar,
  "link_name" varchar,
  "slave_host_name" varchar,
  "master_resource_id" bigint NOT NULL,
  "slave_resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."lvw_logical_link_info" (
  "sub_link_key" varchar,
  "id" bigint NOT NULL,
  "logical_link_topology_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."lvw_logical_link_list" (
  "link_info_id" bigint NOT NULL,
  "logicallinks" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."lvw_logical_link_topology" (
  "id" bigint NOT NULL,
  "ctime" bigint NOT NULL,
  "completetime" bigint,
  "description" varchar,
  "logicalmapid" bigint,
  "mtime" bigint NOT NULL,
  "modifiedby" varchar,
  "name" varchar,
  "optlock" integer,
  "is_running" smallint,
  "starttime" bigint,
  "userid" varchar,
  "role_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."lvw_script_info" (
  "id" bigint NOT NULL,
  "activated" smallint NOT NULL,
  "script" varchar NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."lvw_virtual_link_info" (
  "bidir" smallint,
  "master_if_index" integer,
  "policy" varchar,
  "slave_if_index" integer,
  "sub_link_key" varchar,
  "switch_id" integer,
  "switch_link_name" varchar,
  "virtual_wire" smallint,
  "id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."map_favor" (
  "user_id" varchar NOT NULL,
  "map_id" bigint NOT NULL,
  "priority" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."map_link" (
  "id" bigint NOT NULL,
  "arrowfrom" smallint,
  "arrowto" smallint,
  "bundleexpanded" smallint,
  "ctime" bigint NOT NULL,
  "directiontype" varchar,
  "edgetype" varchar,
  "fontcolor" varchar,
  "fontsize" integer,
  "linecolor" varchar,
  "linestroke" real,
  "linetype" varchar,
  "name" varchar,
  "sourceresourceshowtraffic" smallint,
  "targetresourceshowtraffic" smallint,
  "trafficcolor" varchar,
  "trafficfontsize" integer,
  "zindex" integer,
  "map_id" bigint NOT NULL,
  "source_node_id" bigint NOT NULL,
  "source_resource_id" bigint,
  "target_node_id" bigint NOT NULL,
  "target_resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."map_link_bandwidth_style" (
  "id" bigint NOT NULL,
  "bandwidth" integer,
  "bandwidthraw" bigint,
  "color" varchar,
  "unit" integer,
  "uuid" varchar,
  "width" integer,
  "map_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."map_multi_range" (
  "id" bigint NOT NULL,
  "backgroundcolor" varchar,
  "percentage" integer NOT NULL,
  "rangeorder" integer,
  "map_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."map_node" (
  "id" bigint NOT NULL,
  "backgroundcolor" varchar,
  "backgroundimage" varchar,
  "ctime" bigint NOT NULL,
  "chartmeasurementdefinitionid_1" bigint,
  "chartmeasurementdefinitionid_2" bigint,
  "chartresourceid_1" bigint,
  "chartresourceid_2" bigint,
  "chartresourcetypename_1" varchar,
  "chartresourcetypename_2" varchar,
  "charttextsize" integer,
  "description" varchar,
  "grouplinecolor" varchar,
  "height" integer,
  "httpconnecturl" varchar,
  "httpsconnecturl" varchar,
  "icon" varchar,
  "innermapid" bigint,
  "isexpanded" smallint,
  "isgroup" smallint,
  "issubnetwork" smallint,
  "multiplechangeflag_1" smallint,
  "multiplechangeflag_2" smallint,
  "name" varchar,
  "nodetextcolor" varchar,
  "nodetextpos" varchar,
  "nodetextsize" integer,
  "nodeviewchart" smallint,
  "parentctime" bigint,
  "rotate" real,
  "shownodechart" smallint,
  "subnetworkctime" bigint,
  "uiid" integer,
  "width" integer,
  "x_coord" integer,
  "y_coord" integer,
  "zindex" integer,
  "map_id" bigint NOT NULL,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."map_pathsegment" (
  "link_id" bigint NOT NULL,
  "x" integer NOT NULL,
  "y" integer NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."map_slide_show" (
  "userid" varchar NOT NULL,
  "intervalseconds" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."map_textlabel" (
  "id" bigint NOT NULL,
  "backgroundcolor" varchar,
  "bordercolor" varchar,
  "borderstyle" integer,
  "borderwidth" integer,
  "ctime" bigint NOT NULL,
  "fontbold" smallint NOT NULL,
  "fontcolor" varchar,
  "fontfamily" varchar,
  "fontitalic" smallint NOT NULL,
  "fontsize" integer,
  "height" integer,
  "pattern" varchar,
  "rotate" real,
  "shadowblur" integer,
  "shadowcolor" varchar,
  "shadowoffsetx" integer,
  "shadowoffsety" integer,
  "showtime" smallint,
  "subnetworkctime" bigint,
  "text" varchar,
  "width" integer,
  "x_coord" integer,
  "y_coord" integer,
  "map_id" bigint NOT NULL,
  "textlabelpos" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."map_topology" (
  "id" bigint NOT NULL,
  "acl_id" bigint,
  "alarmdisplaysetting" varchar,
  "alarm_font_size" integer,
  "is_auto_link" smallint,
  "autosize" smallint NOT NULL,
  "backgroundcolor" varchar,
  "backgroundimage" varchar,
  "backgroundimagefilebyte" oid,
  "backgroundimagefilename" varchar,
  "centerx" double precision,
  "centery" double precision,
  "description" varchar,
  "height" integer,
  "linkcolorsetting" varchar,
  "linkwidthsetting" varchar,
  "name" varchar,
  "owneruserid" varchar,
  "recentalarmrefreshminute" integer,
  "rotate" real,
  "scale" double precision,
  "is_shared" smallint,
  "is_show_ip" smallint,
  "is_show_linkeffect" smallint,
  "is_show_traffic" smallint,
  "is_system" smallint,
  "uuid" varchar,
  "width" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."map_topology_slide" (
  "map_slide_id" varchar NOT NULL,
  "map_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."map_topology_subnetwork" (
  "id" bigint NOT NULL,
  "backgroundimage" varchar,
  "backgroundimagefilebyte" oid,
  "backgroundimagefilename" varchar,
  "mapid" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."map_update_history" (
  "id" bigint NOT NULL,
  "ctime" bigint NOT NULL,
  "jsondata" text,
  "map_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."message_acronym" (
  "id" bigint NOT NULL,
  "acronym_desc" varchar,
  "acronym_name" varchar,
  "acronym_type" varchar,
  "acronym_type_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."metricstatisticseconddata" (
  "resource_id" bigint NOT NULL,
  "definition_name" varchar NOT NULL,
  "stat_date" varchar NOT NULL,
  "interval" integer NOT NULL,
  "avg_val" double precision,
  "day_of_week" integer,
  "hour_of_day" integer,
  "max_val" double precision,
  "min_val" double precision,
  "minute_of_hour" integer,
  "second_of_minute" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."mib_file" (
  "id" bigint NOT NULL,
  "defaultmib" smallint,
  "filename" varchar,
  "mibfile" oid
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_arp" (
  "id" bigint NOT NULL,
  "collect_manager_id" varchar,
  "ip_address" varchar,
  "mac_address" varchar NOT NULL,
  "platform_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_bridge" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "platform_id" bigint,
  "port_resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_bridge_addr_list" (
  "bridgeinfo_id" bigint NOT NULL,
  "mac_address" varchar NOT NULL,
  "ip_address" varchar,
  "zone_id" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_default_monitor_mgmt" (
  "id" bigint NOT NULL,
  "apply" smallint NOT NULL,
  "description" varchar,
  "group_name" varchar,
  "name" varchar NOT NULL,
  "vendor" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_dmm_model_list" (
  "defaultmonitormanagement_id" bigint NOT NULL,
  "model_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_dmm_monitor_list" (
  "defaultmonitormanagement_id" bigint NOT NULL,
  "custommonitors" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_identifier" (
  "sysobjectid" varchar NOT NULL,
  "modelname" varchar,
  "topologyicon" varchar,
  "vendor" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_l3ip" (
  "id" bigint NOT NULL,
  "l3ips" varchar,
  "resourceid" bigint,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_link_info" (
  "id" bigint NOT NULL,
  "linktype" integer,
  "locdeviceid" varchar,
  "locportdesc" varchar,
  "locportid" varchar,
  "locsysname" varchar,
  "remdeviceid" varchar,
  "remportdesc" varchar,
  "remportid" varchar,
  "remsysname" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_lldp_link" (
  "id" bigint NOT NULL,
  "locchassisid" varchar,
  "locportdesc" varchar,
  "locportid" varchar,
  "locsysname" varchar,
  "remchassisid" varchar,
  "remportdesc" varchar,
  "remportid" varchar,
  "remsysname" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_mac_addr_fdb_addr_list" (
  "macaddresstable_id" bigint NOT NULL,
  "fdb_addr" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_mac_addr_table" (
  "id" bigint NOT NULL,
  "collect_manager_id" varchar,
  "fdb_port_idx" varchar,
  "if_index" varchar,
  "platform_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_pen" (
  "enterprise_num" integer NOT NULL,
  "alias" varchar,
  "contact_info" varchar,
  "email" varchar,
  "organization_info" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_policy" (
  "id" varchar NOT NULL,
  "dtime" bigint,
  "enterprise_num" integer,
  "organization" varchar,
  "is_use" smallint,
  "policy_dictionary_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_policy_command" (
  "id" varchar NOT NULL,
  "command" varchar,
  "expect" varchar,
  "order_number" integer,
  "is_output" smallint,
  "policy_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_policy_dictionary" (
  "id" varchar NOT NULL,
  "action_guide" varchar,
  "code" varchar,
  "dtime" bigint,
  "group_name" varchar,
  "inspection_standard" varchar,
  "name" varchar,
  "risk" integer,
  "synopsis" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_policy_job" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "is_complete" smallint,
  "dtime" bigint,
  "description" varchar,
  "mtime" bigint,
  "modified_user" varchar,
  "name" varchar,
  "schedule_time" bigint,
  "policy_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_policy_job_resource" (
  "job_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_policy_job_result" (
  "id" bigint NOT NULL,
  "etime" bigint,
  "result_message" varchar,
  "stime" bigint,
  "is_success" smallint,
  "trigger_id" bigint,
  "job_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_policy_job_result_device" (
  "id" bigint NOT NULL,
  "etime" bigint,
  "resource_id" bigint NOT NULL,
  "result_message" text,
  "stime" bigint,
  "is_success" smallint,
  "job_result_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_policy_job_trigger" (
  "id" bigint NOT NULL,
  "c_unix_time" bigint,
  "day_val" integer,
  "day_of_week_val" integer,
  "expression_summary" varchar,
  "hour_val" integer,
  "interval_type" integer,
  "min_val" integer,
  "month_val" integer,
  "s_time" timestamp,
  "job_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_policy_rule" (
  "id" varchar NOT NULL,
  "comparison_operator" varchar,
  "comparison_target" varchar,
  "conjunction" varchar,
  "order_number" integer,
  "policy_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_private_mib" (
  "id" bigint NOT NULL,
  "oid" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_private_oid" (
  "oidkey" varchar NOT NULL,
  "name" varchar,
  "oid" varchar,
  "type" varchar,
  "identifier_id" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_route_table" (
  "id" bigint NOT NULL,
  "collect_manager_id" varchar,
  "net_ip" varchar,
  "netmask" varchar,
  "nexthop" varchar,
  "platform_resource_id" bigint,
  "port_resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_snmp_custom_monitor" (
  "resourcetype" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_snmp_entry_mon" (
  "resourcetype" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_snmp_table_mon" (
  "discoverypolicy" varchar,
  "idcolumn" integer NOT NULL,
  "table_oid" varchar,
  "resourcetype" varchar NOT NULL,
  "entry_monitor_type" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_vlan_associate_ports" (
  "vlaninfo_id" bigint NOT NULL,
  "associateports" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."nms_vlan_info" (
  "id" bigint NOT NULL,
  "resourceid" bigint,
  "vlan_id" varchar,
  "vlan_name" varchar,
  "zoneid" varchar,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."oid_change_info" (
  "object_id" varchar NOT NULL,
  "changed_description" varchar,
  "changed_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."oid_syntax_change_map" (
  "oidchangeinfo_object_id" varchar NOT NULL,
  "name_val" varchar,
  "number_val" varchar NOT NULL,
  "object_id" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."openshift_event_sequence" (
  "openshifts_resource_id" varchar NOT NULL,
  "last_event_time" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."openshift_resource_mapping" (
  "resource_id" bigint NOT NULL,
  "target_name" varchar NOT NULL,
  "source_resource_type" varchar,
  "target_resource_type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."pcm_billing_history" (
  "id" bigint NOT NULL,
  "account_id" varchar,
  "billing_month" integer,
  "cloud_type" varchar NOT NULL,
  "end_time" bigint,
  "monitoring_month" integer,
  "monitoring_time" bigint,
  "resource_id" bigint NOT NULL,
  "service_type" varchar NOT NULL,
  "start_time" bigint NOT NULL,
  "total_cost" integer NOT NULL,
  "unit_cost" integer NOT NULL,
  "user_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."pcm_billing_unit_cost" (
  "id" bigint NOT NULL,
  "cloud_type" varchar NOT NULL,
  "end_time" bigint,
  "service_type" varchar NOT NULL,
  "start_time" bigint NOT NULL,
  "unit_cost" integer NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."pcm_metering_history" (
  "id" bigint NOT NULL,
  "account_id" varchar,
  "cloud_type" varchar NOT NULL,
  "end_reason" varchar,
  "end_time" bigint,
  "end_user_id" varchar,
  "resource_id" bigint NOT NULL,
  "service_type" varchar NOT NULL,
  "start_reason" varchar,
  "start_time" bigint NOT NULL,
  "start_user_id" varchar NOT NULL,
  "unit_cost" integer,
  "user_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."pcm_service_license_info" (
  "resource_id" bigint NOT NULL,
  "cloud_services" varchar,
  "cloud_type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."ping_down_history" (
  "id" bigint NOT NULL,
  "ctime" bigint,
  "err_output" varchar,
  "resource_id" bigint,
  "send_time" bigint,
  "std_output" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."ping_retry" (
  "id" bigint NOT NULL,
  "res_msg" varchar,
  "retry_num" integer,
  "send_time" bigint,
  "timeout_time" bigint,
  "pingdownhistory_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."qrtz_blob_triggers" (
  "sched_name" varchar NOT NULL,
  "trigger_name" varchar NOT NULL,
  "trigger_group" varchar NOT NULL,
  "blob_data" bytea
);

CREATE TABLE IF NOT EXISTS "polestar"."qrtz_calendars" (
  "sched_name" varchar NOT NULL,
  "calendar_name" varchar NOT NULL,
  "calendar" bytea NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."qrtz_cron_triggers" (
  "sched_name" varchar NOT NULL,
  "trigger_name" varchar NOT NULL,
  "trigger_group" varchar NOT NULL,
  "cron_expression" varchar NOT NULL,
  "time_zone_id" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."qrtz_fired_triggers" (
  "sched_name" varchar NOT NULL,
  "entry_id" varchar NOT NULL,
  "trigger_name" varchar NOT NULL,
  "trigger_group" varchar NOT NULL,
  "instance_name" varchar NOT NULL,
  "fired_time" bigint NOT NULL,
  "sched_time" bigint NOT NULL,
  "priority" integer NOT NULL,
  "state" varchar NOT NULL,
  "job_name" varchar,
  "job_group" varchar,
  "is_nonconcurrent" boolean,
  "requests_recovery" boolean
);

CREATE TABLE IF NOT EXISTS "polestar"."qrtz_job_details" (
  "sched_name" varchar NOT NULL,
  "job_name" varchar NOT NULL,
  "job_group" varchar NOT NULL,
  "description" varchar,
  "job_class_name" varchar NOT NULL,
  "is_durable" boolean NOT NULL,
  "is_nonconcurrent" boolean NOT NULL,
  "is_update_data" boolean NOT NULL,
  "requests_recovery" boolean NOT NULL,
  "job_data" bytea
);

CREATE TABLE IF NOT EXISTS "polestar"."qrtz_locks" (
  "sched_name" varchar NOT NULL,
  "lock_name" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."qrtz_paused_trigger_grps" (
  "sched_name" varchar NOT NULL,
  "trigger_group" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."qrtz_scheduler_state" (
  "sched_name" varchar NOT NULL,
  "instance_name" varchar NOT NULL,
  "last_checkin_time" bigint NOT NULL,
  "checkin_interval" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."qrtz_simple_triggers" (
  "sched_name" varchar NOT NULL,
  "trigger_name" varchar NOT NULL,
  "trigger_group" varchar NOT NULL,
  "repeat_count" bigint NOT NULL,
  "repeat_interval" bigint NOT NULL,
  "times_triggered" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."qrtz_simprop_triggers" (
  "sched_name" varchar NOT NULL,
  "trigger_name" varchar NOT NULL,
  "trigger_group" varchar NOT NULL,
  "str_prop_1" varchar,
  "str_prop_2" varchar,
  "str_prop_3" varchar,
  "int_prop_1" integer,
  "int_prop_2" integer,
  "long_prop_1" bigint,
  "long_prop_2" bigint,
  "dec_prop_1" numeric,
  "dec_prop_2" numeric,
  "bool_prop_1" boolean,
  "bool_prop_2" boolean
);

CREATE TABLE IF NOT EXISTS "polestar"."qrtz_triggers" (
  "sched_name" varchar NOT NULL,
  "trigger_name" varchar NOT NULL,
  "trigger_group" varchar NOT NULL,
  "job_name" varchar NOT NULL,
  "job_group" varchar NOT NULL,
  "description" varchar,
  "next_fire_time" bigint,
  "prev_fire_time" bigint,
  "priority" integer,
  "trigger_state" varchar NOT NULL,
  "trigger_type" varchar NOT NULL,
  "start_time" bigint NOT NULL,
  "end_time" bigint,
  "calendar_name" varchar,
  "misfire_instr" smallint,
  "job_data" bytea
);

CREATE TABLE IF NOT EXISTS "polestar"."realtime_dashboard" (
  "id" bigint NOT NULL,
  "description" varchar,
  "is_view" smallint,
  "layout" integer NOT NULL,
  "name" varchar,
  "user_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."realtime_dashboard_resources" (
  "realtimedashboardinfo_id" bigint NOT NULL,
  "resource_order" integer,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_definition" (
  "id" bigint NOT NULL,
  "acl_id" bigint,
  "is_attention" smallint,
  "business_schedule_id" bigint,
  "is_clear" smallint,
  "is_critical" smallint,
  "dtime" bigint,
  "day_of_month" integer,
  "day_of_week" integer,
  "desc2_content" varchar,
  "desc2_title" varchar,
  "desc_content" varchar,
  "desc_title" varchar,
  "is_error" smallint,
  "is_fatal" smallint,
  "hour_of_day" integer,
  "interval_type" varchar,
  "is_doc" smallint,
  "is_hwp" smallint,
  "is_nxl" smallint,
  "is_pdf" smallint,
  "is_ppt" smallint,
  "is_xls" smallint,
  "is_xlsx" smallint,
  "last_sched_time" bigint,
  "is_mail" smallint,
  "mail_contents" varchar,
  "mail_title" varchar,
  "minute_of_hour" integer,
  "next_sched_time" bigint,
  "ownerid" varchar,
  "recenthour" integer,
  "reportname" varchar,
  "reportprovider" varchar,
  "reporttitle" varchar,
  "is_running" smallint,
  "schedule_type" varchar,
  "searchfrom" bigint,
  "searchto" bigint,
  "searchtype" varchar,
  "topn" integer,
  "is_trouble" smallint,
  "user_search" varchar,
  "uuid" varchar,
  "template_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_document" (
  "id" bigint NOT NULL,
  "doc_array" oid,
  "ctime" bigint,
  "file_size" integer,
  "reportformat" varchar,
  "history_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_history" (
  "id" bigint NOT NULL,
  "dtime" bigint,
  "end_time" bigint,
  "issue_time" bigint,
  "reason" varchar,
  "start_time" bigint,
  "is_success" smallint,
  "took_time" bigint,
  "rep_def_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_inst_send" (
  "id" bigint NOT NULL,
  "is_instant" smallint,
  "manual_input_user" varchar,
  "report_def_id" bigint,
  "is_report_target" smallint
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_inst_user" (
  "reportinstantsend_id" bigint NOT NULL,
  "selectedusers" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_md_target" (
  "report_def_id" bigint NOT NULL,
  "measure_def_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_noti_role" (
  "reportdefinition_id" bigint NOT NULL,
  "targetroles" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_noti_user" (
  "reportdefinition_id" bigint NOT NULL,
  "targetusers" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_noti_usergroup" (
  "reportdefinition_id" bigint NOT NULL,
  "targetusergroups" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_resource_exclude" (
  "report_def_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_resource_target" (
  "report_def_id" bigint NOT NULL,
  "resource_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."rep_template" (
  "dtype" varchar NOT NULL,
  "templatekey" varchar NOT NULL,
  "is_alarm_grade" smallint,
  "is_business_schedule" smallint,
  "is_child_resource" smallint,
  "dtime" bigint,
  "description" varchar,
  "domain" varchar,
  "is_exclude_resource" smallint,
  "is_measure_def" smallint,
  "name" varchar,
  "is_period" smallint,
  "period_term" varchar,
  "rep_category" varchar,
  "is_resource" smallint,
  "is_topn" smallint,
  "is_user_desc" smallint,
  "is_user_search" smallint,
  "odi" oid,
  "ozr" oid
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_duplication_login" (
  "id" bigint NOT NULL,
  "login_date" varchar NOT NULL,
  "system_id" varchar NOT NULL,
  "user_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_profile" (
  "id" bigint NOT NULL,
  "create_date" varchar,
  "create_user" varchar,
  "last_change_user" varchar,
  "profile_desc" varchar,
  "profile_name" varchar NOT NULL,
  "profile_type" varchar,
  "system_id" varchar NOT NULL,
  "update_date" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_profile_relation" (
  "id" bigint NOT NULL,
  "composite_profile" varchar NOT NULL,
  "single_profile" varchar NOT NULL,
  "system_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_role" (
  "id" bigint NOT NULL,
  "change_date" varchar,
  "change_user" varchar,
  "is_composite" smallint,
  "create_date" varchar,
  "create_user" varchar,
  "parent_role" varchar,
  "profile_desc" varchar,
  "profile_name" varchar,
  "role_desc" varchar,
  "role_name" varchar NOT NULL,
  "status_color" varchar,
  "status_message" varchar,
  "system_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_role_relation" (
  "id" bigint NOT NULL,
  "composite_role" varchar NOT NULL,
  "single_role" varchar NOT NULL,
  "system_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_tcode" (
  "id" bigint NOT NULL,
  "system_id" varchar NOT NULL,
  "tcode" varchar NOT NULL,
  "tcode_desc" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_tcode_profile" (
  "id" bigint NOT NULL,
  "profile_name" varchar NOT NULL,
  "system_id" varchar NOT NULL,
  "tcode_high" varchar,
  "tcode_low" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_tcode_role" (
  "id" bigint NOT NULL,
  "role_name" varchar NOT NULL,
  "system_id" varchar NOT NULL,
  "tcode" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_used_tcode" (
  "id" bigint NOT NULL,
  "system_id" varchar NOT NULL,
  "tcode" varchar NOT NULL,
  "used_date" varchar NOT NULL,
  "user_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_user" (
  "id" bigint NOT NULL,
  "create_date" varchar,
  "creator" varchar,
  "last_pw_changetime" varchar,
  "last_logontime" varchar,
  "lock_status" varchar,
  "logon_fails" varchar,
  "resource_id" bigint,
  "room_number" varchar,
  "system_id" varchar NOT NULL,
  "user_class" varchar,
  "user_id" varchar NOT NULL,
  "user_type" varchar,
  "user_name" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_user_profile" (
  "id" bigint NOT NULL,
  "profile_name" varchar NOT NULL,
  "system_id" varchar NOT NULL,
  "user_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."sap_user_role" (
  "id" bigint NOT NULL,
  "role_end_date" varchar,
  "role_name" varchar NOT NULL,
  "role_start_date" varchar,
  "system_id" varchar NOT NULL,
  "user_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."serv_port_def" (
  "id" bigint NOT NULL,
  "def_port" integer,
  "serv_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."server_default_port_permission" (
  "port_number" integer NOT NULL,
  "protocol_type" varchar NOT NULL,
  "ctime" bigint,
  "define_type" varchar NOT NULL,
  "dtime" bigint,
  "permission_yn" varchar NOT NULL,
  "port_desc" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."server_port_permission" (
  "resource_id" bigint NOT NULL,
  "port_number" integer NOT NULL,
  "protocol_type" varchar NOT NULL,
  "ctime" bigint NOT NULL,
  "define_type" varchar NOT NULL,
  "dtime" bigint,
  "permission_yn" varchar NOT NULL,
  "port_desc" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_active_agent" (
  "id" bigint NOT NULL,
  "agentid" varchar NOT NULL,
  "agentversion" varchar,
  "connectedap" varchar,
  "connectiontime" bigint,
  "hostname" varchar,
  "icon" varchar,
  "ipaddress" varchar,
  "isregisted" smallint NOT NULL,
  "ostype" varchar NOT NULL,
  "registrationtime" bigint,
  "serverassetno" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_agent_acl_list" (
  "id" bigint NOT NULL,
  "aclinfo" varchar,
  "agentacltype" integer,
  "ctime" bigint NOT NULL,
  "defaultvalue" smallint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_agent_index_mapping" (
  "mapping_index" integer NOT NULL,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_agent_index_mapping_id" (
  "mapping_index" varchar,
  "next_index" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_agent_install_file_info" (
  "file_name" varchar NOT NULL,
  "description" varchar,
  "update_file" oid,
  "file_size" integer,
  "manager_name" varchar,
  "ostype" varchar,
  "upload_time" bigint,
  "version" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_agent_patch_history" (
  "id" bigint NOT NULL,
  "action_status" smallint,
  "agent_version" varchar,
  "dtime" bigint,
  "execute_status" varchar,
  "failure_reason" varchar,
  "patch_log" text,
  "patch_result" varchar,
  "resource_id" bigint,
  "took_time" bigint,
  "update_time" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_agent_update_file_info" (
  "ostype" varchar NOT NULL,
  "description" varchar,
  "update_file" oid,
  "file_name" varchar,
  "file_size" integer,
  "reasontype" varchar,
  "upload_time" bigint,
  "version" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_agent_update_history" (
  "id" bigint NOT NULL,
  "dtime" bigint,
  "message" varchar,
  "resourceid" bigint,
  "is_success" smallint,
  "took_time" bigint,
  "update_time" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_agent_version_info" (
  "platform_resource_id" bigint NOT NULL,
  "agent_version" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_default_file_monitor" (
  "id" bigint NOT NULL,
  "apply" smallint NOT NULL,
  "backuppath" varchar,
  "description" varchar,
  "filename" varchar NOT NULL,
  "icon" varchar,
  "name" varchar NOT NULL,
  "ostype" varchar NOT NULL,
  "strongtype" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_default_log_monitor" (
  "id" bigint NOT NULL,
  "apply" smallint NOT NULL,
  "casesensitive" smallint,
  "customencodingtype" varchar,
  "debug" varchar,
  "description" varchar,
  "encodingtype" varchar,
  "error" varchar,
  "fatal" varchar,
  "icon" varchar,
  "info" varchar,
  "logfile" varchar,
  "logtype" varchar,
  "matchingtype" varchar,
  "name" varchar NOT NULL,
  "ostype" varchar NOT NULL,
  "resource_type" varchar NOT NULL,
  "scantype" varchar,
  "warn" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_default_process_monitor" (
  "id" bigint NOT NULL,
  "apply" smallint NOT NULL,
  "description" varchar,
  "icon" varchar,
  "matchingfield" varchar,
  "matchingtype" varchar,
  "name" varchar NOT NULL,
  "ostype" varchar NOT NULL,
  "owner" varchar,
  "processname" varchar NOT NULL,
  "runningaccount" varchar,
  "startcmd" varchar,
  "stopcmd" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_default_win_monitor" (
  "id" bigint NOT NULL,
  "apply" smallint NOT NULL,
  "description" varchar,
  "name" varchar NOT NULL,
  "ostype" varchar NOT NULL,
  "resource_type" varchar NOT NULL,
  "servicename" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_proc_version" (
  "ip" varchar NOT NULL,
  "process_name" varchar NOT NULL,
  "prev_version" varchar,
  "recent_version" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_script_custom_monitor" (
  "defaulttimeoutsec" integer NOT NULL,
  "delimiter" varchar,
  "script_body" text,
  "scripttype" varchar,
  "resourcetype" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_upload_file_hist" (
  "uploadfilehistory_id" bigint NOT NULL,
  "transferfiles" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_upload_file_info" (
  "file_id" integer NOT NULL,
  "upload_file" oid,
  "file_description" varchar,
  "file_name" varchar,
  "file_path" varchar,
  "file_size" bigint,
  "is_override" smallint,
  "uuid" varchar,
  "upload_file_job_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_upload_file_job" (
  "id" bigint NOT NULL,
  "job_date" bigint,
  "job_description" varchar,
  "job_name" varchar,
  "user_id" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_upload_history" (
  "id" bigint NOT NULL,
  "job_end_time" bigint,
  "job_id" bigint,
  "job_name" varchar,
  "job_start_time" bigint,
  "user_id" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_upload_loc_hist" (
  "location_id" bigint NOT NULL,
  "resource_id" bigint,
  "resource_name" varchar,
  "history_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_upload_location" (
  "location_id" bigint NOT NULL,
  "resource_id" bigint,
  "resource_name" varchar,
  "upload_file_job_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."sms_upload_target_hist" (
  "id" bigint NOT NULL,
  "endtime" bigint,
  "resourceid" bigint,
  "starttime" bigint,
  "succ" smallint,
  "sms_upload_hist_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."snmp_custom_monitor" (
  "resourcetype" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."snmp_entry_mon" (
  "resourcetype" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."snmp_table_mon" (
  "discoverypolicy" varchar,
  "idcolumn" integer NOT NULL,
  "table_oid" varchar,
  "resourcetype" varchar NOT NULL,
  "entry_monitor_type" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."stat_favor" (
  "id" bigint NOT NULL,
  "description" varchar,
  "is_favorite" smallint,
  "period_mode" integer,
  "name" varchar,
  "owneruserid" varchar,
  "is_shared" smallint,
  "statisticsmode" varchar,
  "business_time_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."stat_favor_business_time" (
  "id" bigint NOT NULL,
  "from_hour" integer,
  "to_hour" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."stat_favor_business_week" (
  "business_time_id" bigint NOT NULL,
  "day_of_week" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."stat_favor_cond_dids" (
  "favoritestatisticscondition_id" bigint NOT NULL,
  "measurementdefinitionids" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."stat_favor_cond_rids" (
  "favoritestatisticscondition_id" bigint NOT NULL,
  "resourceids" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."stat_favor_condition" (
  "id" bigint NOT NULL,
  "is_bandwidth" smallint,
  "is_init" smallint,
  "is_single" smallint,
  "resource_type" varchar,
  "row_order" integer,
  "temp_id" varchar NOT NULL,
  "stat_id" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."syslog_event_job" (
  "id" bigint NOT NULL,
  "description" varchar,
  "is_enabled" smallint,
  "expect_script_job_id" varchar,
  "expect_script_job_name" varchar,
  "included_pattern" varchar,
  "name" varchar,
  "vendor_name" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."trap_definition" (
  "trap_oid" varchar NOT NULL,
  "is_include_all" smallint,
  "is_include_descr" smallint
);

CREATE TABLE IF NOT EXISTS "polestar"."trap_event_severity_map" (
  "trapdefinition_trap_oid" varchar NOT NULL,
  "event_severity" integer,
  "is_exact_match" smallint,
  "object_id" varchar,
  "temp_id" varchar NOT NULL,
  "trap_value" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."trap_include_map" (
  "trapdefinition_trap_oid" varchar NOT NULL,
  "is_include" smallint,
  "object_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."trap_specific" (
  "specificoid" varchar NOT NULL,
  "description" varchar,
  "severity" varchar,
  "specificname" varchar,
  "vendor" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."vmm_alarm_log" (
  "id" bigint NOT NULL,
  "lasttime" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."vmm_event_log" (
  "id" bigint NOT NULL,
  "lasttime" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."vmm_task_log" (
  "id" bigint NOT NULL,
  "lastcomplectdtime" bigint NOT NULL,
  "laststarttime" bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."was_connection" (
  "id" bigint NOT NULL,
  "connection_type" varchar,
  "db_user_name" varchar,
  "db_user_pwd" varchar,
  "defaultwasconnection" smallint NOT NULL,
  "description" varchar,
  "db_connection_size" integer,
  "db_connection_time" integer,
  "jdbc_driver" varchar,
  "jdbc_url" varchar,
  "jennifer_domain_id" varchar,
  "jennifer_token" varchar,
  "jennifer_url" varchar,
  "jennifer_version" varchar,
  "name" varchar NOT NULL,
  "db_sql" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."was_dashboard" (
  "id" bigint NOT NULL,
  "instance" varchar NOT NULL,
  "resource_id" bigint NOT NULL,
  "user_id" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."was_instance_abbreviation" (
  "id" bigint NOT NULL,
  "name" varchar,
  "user_id" varchar NOT NULL,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."was_instance_group" (
  "id" bigint NOT NULL,
  "authority" varchar,
  "description" varchar,
  "name" varchar,
  "user_id" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."was_instance_resource" (
  "instancegroup_id" bigint NOT NULL,
  "is_check" smallint,
  "resource_order" integer,
  "resource_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."was_object" (
  "obj_hash" integer NOT NULL,
  "ip" varchar NOT NULL,
  "agent_id" varchar,
  "hostname" varchar NOT NULL,
  "manager_id" varchar,
  "obj_name" varchar NOT NULL,
  "obj_type" varchar NOT NULL,
  "first_conn_time" bigint,
  "version" varchar,
  "wakeup" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."was_object_datasource" (
  "obj_hash" integer NOT NULL,
  "obj_name" varchar NOT NULL,
  "obj_type" varchar NOT NULL,
  "first_conn_time" bigint,
  "instance_id" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."was_search_condition" (
  "id" bigint NOT NULL,
  "application" varchar,
  "clientip" varchar,
  "error" smallint NOT NULL,
  "lastapplied" smallint NOT NULL,
  "maxvalue" varchar,
  "minvalue" varchar,
  "name" varchar,
  "showmaxvalue" smallint NOT NULL,
  "showtooltip" smallint,
  "showtotalcount" smallint,
  "timeperiod" integer NOT NULL,
  "type" varchar,
  "user_id" varchar NOT NULL,
  "ytickinterval" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."was_search_instance" (
  "instance_id" bigint NOT NULL,
  "instanceids" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."was_search_tree" (
  "tree_id" bigint NOT NULL,
  "treeids" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."webapm_agent" (
  "agent_key" varchar NOT NULL,
  "agent_version" varchar,
  "browser_setting" text,
  "ctime" bigint NOT NULL,
  "collect_interval" integer,
  "dns_address" varchar,
  "hostname" varchar,
  "install_path" varchar,
  "installed_browser_info" varchar,
  "ipaddress" varchar,
  "mtime" bigint NOT NULL,
  "os_arch" varchar,
  "os_version" varchar,
  "remove_url" text,
  "resource_status" varchar,
  "agent_start_time" bigint,
  "use_putid" smallint,
  "use_remove_url" smallint,
  "was_connection_key" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."webapm_command" (
  "id" bigint NOT NULL,
  "command_type" varchar NOT NULL,
  "ex_sequence" integer,
  "command_index" integer,
  "target" varchar,
  "time_stamp" bigint,
  "command_used" smallint,
  "value" varchar,
  "script_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."webapm_command_type" (
  "name" varchar NOT NULL,
  "command" varchar,
  "param" integer
);

CREATE TABLE IF NOT EXISTS "polestar"."webapm_script" (
  "id" bigint NOT NULL,
  "description" varchar,
  "name" varchar NOT NULL
);

CREATE TABLE IF NOT EXISTS "polestar"."widget" (
  "widget_id" varchar NOT NULL,
  "h" integer,
  "icon" varchar,
  "min_h" integer,
  "min_w" integer,
  "name" varchar,
  "request_method" varchar,
  "request_url" varchar,
  "w" integer,
  "widget_type" varchar
);

CREATE TABLE IF NOT EXISTS "polestar"."widget_dashboard" (
  "id" bigint NOT NULL,
  "is_bookmark" smallint,
  "date_created" bigint,
  "description" varchar,
  "group_name" varchar,
  "date_modified" bigint,
  "name" varchar,
  "owner_user_id" varchar,
  "property" text
);

CREATE TABLE IF NOT EXISTS "polestar"."widget_instance" (
  "widget_instance_id" bigint NOT NULL,
  "is_blocked" smallint,
  "h" integer,
  "i" varchar,
  "min_h" integer,
  "min_w" integer,
  "is_moved" smallint,
  "property" text,
  "w" integer,
  "name" varchar,
  "x" integer,
  "y" integer,
  "widget_id" varchar,
  "widget_dashboard_id" bigint
);

CREATE TABLE IF NOT EXISTS "polestar"."widget_type" (
  "widget_type" varchar NOT NULL,
  "description" varchar
);
