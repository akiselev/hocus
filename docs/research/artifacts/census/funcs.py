import re,sys,collections
src=open(sys.argv[1],encoding='utf-8',errors='replace').read().split('\n')
# find function definitions: a line at column 0 starting with identifier/type, containing '(' and the function body '{' at col 0 on a later line before ';'
funcs=[]
i=0
pat=re.compile(r'^(?:static\s+)?(?:inline\s+)?(?:__exception\s+|no_inline\s+|force_inline\s+|__maybe_unused\s+|warn_unused\s+)*[A-Za-z_][\w\s\*]*?\b([A-Za-z_]\w*)\s*\(')
n=len(src)
while i<n:
    l=src[i]
    m=pat.match(l)
    if m and not l.startswith(('typedef','#','struct ','enum ','union ')) and not l.rstrip().endswith(';'):
        # find '{' at col0 within next 6 lines with no ';' at line end before
        j=i; ok=False
        while j<min(n,i+8):
            if src[j].startswith('{'): ok=True;break
            if src[j].rstrip().endswith(';'): break
            j+=1
        if ok:
            # find end: line '}' at col 0
            k=j+1
            while k<n and not src[k].startswith('}'): k+=1
            funcs.append((m.group(1),i+1,k+1))
            i=k+1; continue
    i+=1
print(len(funcs), sum(e-s+1 for _,s,e in funcs))
groups=[
 ('interpreter JS_CallInternal', r'^JS_CallInternal$'),
 ('parser/tokenizer', r'^(js_parse|next_token|js_parse_|simple_next_token|ident_|skip_|json_next_token|json_parse|js_parse_template|peek_token|token_|re_parse_out|js_parse_string|js_parse_regexp|update_token)'),
 ('compiler/emitter/resolver', r'^(emit_|push_scope|pop_scope|add_var|add_arg|find_var|resolve_|optimize_|skip_dead|code_|new_label|update_label|ss_check|compute_stack|add_closure|close_scopes|get_closure_var|instantiate_hoisted|js_create_function|js_free_function_def|js_new_function_def|add_scope_var|define_var|set_object_name|put_short_code|is_label|get_prev_opcode|cpool_add|emit|ls_|lvalue|js_compile|js_emit|mark_eval|add_module_variables|js_closure|js_link|js_resolve|js_module_ns|js_create_module|js_inner_module|js_evaluate_module|js_async_module|exec_module|js_build_module|js_free_module|new_module|add_req_module|add_export|add_import|add_star_export|find_export|js_get_module_ns|js_execute|js_load_module|js_host_resolve)'),
 ('gc/refcount/memory', r'^(gc_|mark_|free_|__JS_FreeValue|js_free_value|JS_RunGC|JS_MarkValue|js_trigger_gc|reset_weak|js_weakref|delete_weak|js_def_malloc|js_malloc|js_realloc|js_free|JS_Free|js_mark|add_gc|remove_gc|decref|JS_ComputeMemory|JS_DumpMemory|js_weak|finrec|js_finrec|is_live)'),
 ('objects/shapes/properties', r'^(js_create_shape|js_new_shape|js_free_shape|resize_properties|compact_properties|add_shape_property|find_own_property|find_hashed_shape|js_shape_|JS_DefineProperty|JS_GetProperty|JS_SetProperty|JS_DeleteProperty|JS_GetOwnProperty|JS_HasProperty|add_property|delete_property|js_update_property|convert_fast_array|expand_fast_array|set_array_length|JS_NewObject|JS_SetPrototype|JS_GetPrototype|js_get_length|JS_PreventExtensions|JS_IsExtensible|js_define_|JS_CreateProperty|js_create_property|free_property|JS_GetOwnPropertyNames|js_get_own_property|set_property|get_own_property|js_instantiate|JS_InstantiateFunctionListItem|JS_SetPropertyFunctionList|js_object_list|js_free_prop|JS_NewObjectFromShape|JS_NewObjectProto)'),
 ('atoms/strings core', r'^(JS_NewAtom|JS_FreeAtom|__JS_NewAtom|__JS_FindAtom|js_free_atom|JS_AtomGet|JS_AtomTo|js_atom|JS_ResizeAtomHash|js_get_atom_index|string_|js_new_string|js_alloc_string|JS_ConcatString|js_string_|JS_NewString|js_sub_string|StringBuffer|js_new_rope|rope|JS_ToCString|JS_FreeCString|js_strcmp|js_string_compare|JS_ConcatStrings|js_concat)'),
 ('conversions/operators/numbers', r'^(JS_To|js_to|JS_Is|js_is|js_strict_eq|js_same_value|js_operator|js_binary_|js_unary_|js_add_slow|js_relational|js_eq_slow|js_not_slow|js_dtoa|js_atof|js_number_|js_fcvt|js_ecvt|i64toa|u32toa|u64toa|js_bigint|JS_NewBigInt|js_ToBigInt|js_compare_bigint|js_poll_interrupts|js_check_stack)'),
 ('Array/iterators', r'^(js_array|js_create_array|js_iterator|js_create_iterator|js_array_iterator|js_build_arguments|js_build_rest|js_build_mapped|js_regexp_string_iterator|js_create_from_ctor|js_allocate_fast_array|js_array_lastIndexOf|js_iterator_helper|js_iterator_wrap|js_iterator_concat|js_create_array_iterator)'),
 ('TypedArray/ArrayBuffer/DataView/Atomics', r'^(js_typed_array|js_array_buffer|js_dataview|js_atomics|typed_array|array_buffer|js_TA_|js_sab|JS_NewArrayBuffer|JS_GetArrayBuffer|JS_NewTypedArray|JS_GetTypedArray|js_create_typed_array|get_typed_array|validate_typed_array|js_new_uint8array|js_uint8array|js_float16|js_array_buffer_)'),
 ('String/RegExp builtins', r'^(js_string|js_regexp|js_RegExp|JS_RegExp|lre_|js_create_regexp|js_new_regexp|js_compile_regexp|js_symbol|js_get_substitution|String_|js_lastIndexOf)'),
 ('Promise/async/generators/jobs', r'^(js_promise|js_async|js_generator|async_func|js_create_resolving|perform_promise|promise_|js_new_promise|JS_NewPromise|JS_EnqueueJob|JS_ExecutePendingJob|js_await|js_microtask|fulfill_|reject_|js_free_generator|free_generator|js_create_async|js_async_from_sync|js_async_generator|js_promise_resolve)'),
 ('Map/Set/WeakRef/FinReg', r'^(js_map|map_|js_set_|js_weakref|js_finrec|map_find|map_add|map_delete|js_create_map|js_free_map)'),
 ('Proxy/Reflect', r'^(js_proxy|js_reflect|get_proxy)'),
 ('Object/Function/Error/Symbol/Global/Math/Date/JSON builtins', r'^(js_object|js_function|js_error|js_aggregate|js_global|js_math|js_date|js_json|JS_JSON|js_boolean|js_number|js_bigint_|js_symbol|js_callsite|js_array_from|get_date|set_date|js_Date|js_os|js_std|js_build_backtrace|build_backtrace|js_throw|JS_Throw|js_disposable|js_suppressed|js_domexception|js_performance|js_navigator|js_queue)'),
 ('bytecode reader/writer (bjson)', r'^(JS_ReadObject|JS_WriteObject|bc_|js_bjson|JS_ReadFunction|JS_WriteFunction|js_object_write|js_object_read|JS_ReadString|JS_Read|JS_Write)'),
 ('runtime/context/api', r'^(JS_NewRuntime|JS_FreeRuntime|JS_NewContext|JS_FreeContext|JS_AddIntrinsic|JS_SetRuntime|JS_GetRuntime|JS_SetContext|JS_Set|JS_Get|JS_New|JS_Eval|JS_Call|JS_Invoke|JS_Construct|JS_Dup|JS_Enable|JS_Is)'),
]
cnt=collections.OrderedDict((g,[0,0]) for g,_ in groups); cnt['other']=[0,0]
other=[]
for name,s,e in funcs:
    for g,p in groups:
        if re.match(p,name): cnt[g][0]+=1; cnt[g][1]+=e-s+1; break
    else:
        cnt['other'][0]+=1; cnt['other'][1]+=e-s+1; other.append((e-s+1,name))
for g,(c,l) in cnt.items(): print(f"{l:6d} lines {c:5d} fns  {g}")
other.sort(reverse=True); print(other[:40])
