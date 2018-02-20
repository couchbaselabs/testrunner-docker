class HANDLER_CODE:
    BUCKET_OPS_ON_UPDATE = 'handler_code/bucket_op_on_update.js'
    BUCKET_OPS_ON_DELETE = 'handler_code/bucket_op_on_delete.js'
    N1QL_INSERT_ON_UPDATE = 'handler_code/n1ql_insert_on_update.js'
    N1QL_INSERT_ON_UPDATE_WITH_DOC_TIMER = 'handler_code/n1ql_insert_with_doc_timer.js'
    N1QL_INSERT_ON_UPDATE_WITH_CRON_TIMER = 'handler_code/n1ql_insert_with_cron_timer.js'
    BUCKET_OPS_WITH_DOC_TIMER = 'handler_code/bucket_op_with_doc_timer.js'
    BUCKET_OPS_WITH_CRON_TIMER = 'handler_code/bucket_op_with_cron_timer.js'
    BUCKET_OPS_WITH_CRON_TIMER_WITH_SECOND_BUCKET = 'handler_code/bucket_op_on_second_bucket_with_cron_timer.js'
    DELETE_BUCKET_OP_ON_DELETE = 'handler_code/delete_doc_bucket_op.js'
    DELETE_BUCKET_OP_ON_DELETE1 = 'handler_code/delete_doc_bucket_op1.js'
    BUCKET_OPS_WITH_UNICODE_CHAR = 'handler_code/bucket_op_with_unicode_characters.js'
    DELETE_BUCKET_OP_ON_DELETE_INTERCHAGE = 'handler_code/delete_doc_bucket_op_interchange.js'
    MULTIPLE_BUCKET_OPS_ON_UPDATE = 'handler_code/multiple_bucket_op_on_update.js'
    MULTIPLE_TIMER_OPS_OF_DIFFERENT_TYPE_ON_UPDATE = 'handler_code/multiple_timer_operations_on_update.js'
    MULTIPLE_TIMER_OPS_OF_SAME_TYPE_ON_UPDATE = 'handler_code/multiple_timer_operations_of_same_type_on_update.js'
    MULTIPLE_ALIAS_BINDINGS_FOR_SAME_BUCKET = 'handler_code/multiple_alias_bindings_for_same_bucket.js'
    BUCKET_OPS_WITH_TIMERS = 'handler_code/bucket_op_with_timers.js'
    BUCKET_OPS_WITH_CRON_TIMERS = 'handler_code/bucket_op_with_cron_timers.js'
    READ_BUCKET_OP_ON_DST = 'handler_code/read_bucket_on_dst_bucket.js'
    N1QL_OPS_WITH_TIMERS = 'handler_code/n1ql_op_with_timers.js'
    N1QL_DELETE_UPDATE = 'handler_code/n1ql_delete_update.js'
    N1QL_PREPARE = 'handler_code/n1ql_prepare.js'
    N1QL_DDL = 'handler_code/n1ql_DDL.js'
    N1QL_DML = 'handler_code/n1ql_DML.js'
    SYNTAX_ERROR = 'handler_code/syntax_error.js'
    RECURSIVE_MUTATION = 'handler_code/recursive_mutation.js'
    GRANT_REVOKE = 'handler_code/grant_revoke.js'
    CURL = 'handler_code/curl.js'
    ANONYMOUS = 'handler_code/anonymous.js'
    RECURSION_FUNCTION = 'handler_code/recursion.js'
    N1QL_TEMP = 'handler_code/n1ql_temp.js'
    N1QL = 'rqg_handler_code/n1ql.js'
    N1QL_TEMP_PATH = 'handler_code/rqg_handler_code/'
    N1QL_UPDATE_DELETE = 'handler_code/n1ql_update_delete.js'
    N1QL_ITERATOR = 'handler_code/n1ql_iterator.js'
    N1QL_ITERATORS = 'handler_code/n1ql_iterators.js'
    SRC_AND_DST_BUCKET_ARE_SAME='handler_code/src_and_dst_bucket_are_same.js'

class HANDLER_CODE_ERROR:
    N1QL_SYNTAX='handler_code/handler_code_error/n1ql_syntax.js'
    GLOBAL_VARIABLE='handler_code/handler_code_error/global_variable.js'
    EMPTY='handler_code/handler_code_error/empty.js'
    RANDOM='handler_code/handler_code_error/random_method.js'
    ANONYMOUS_DOC_TIMER='handler_code/handler_code_error/anonymous_function_doc_timer.js'
    ANONYMOUS_CRON_TIMER='handler_code/handler_code_error/anonymous_function_cron_timer.js'
    EXECUTION_TIME_MORE_THAN_TIMEOUT = 'handler_code/handler_code_error/code_execution_more_than_execution_timeout.js'


class EXPORTED_FUNCTION:
    N1QL_INSERT_ON_UPDATE_WITH_CRON_TIMER = 'exported_functions/n1ql_insert_with_cron_timer.json'