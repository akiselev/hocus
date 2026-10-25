import sys,re
def cat(op):
    if re.match(r'(get|put|set)_(loc|arg|var_ref)\d*(_check|8|16)?$|^(get|put|set)_loc(0_loc1)?|^close_loc|^get_loc_checkthis', op): 
        return 'var_ref' if 'var_ref' in op else 'local/arg move'
    if op in ('dup','dup1','dup2','dup3','drop','nip','nip1','swap','swap2','rot3l','rot3r','rot4l','rot5l','perm3','perm4','perm5','insert2','insert3','insert4','nop'): return 'stack shuffle'
    if op.startswith('push_') or op in ('undefined','null','push_this','push_false','push_true','fclosure','fclosure8','object','special_object'): return 'push const/this'
    if op.startswith('if_') or op in ('goto','goto8','goto16'): return 'branch'
    if op in ('get_field','get_field2','put_field','get_array_el','get_array_el2','put_array_el','get_length','get_private_field','put_private_field','define_field','get_ref_value','put_ref_value','get_super','to_propkey','to_propkey2','in','delete','private_in','get_array_el3'): return 'property access'
    if op.startswith('call') or op.startswith('tail_call') or op in ('apply','array_from','return','return_undef','return_async','apply_constructor','call_constructor'): return 'call/return'
    if op in ('add','sub','mul','div','mod','and','or','xor','shl','sar','shr','lnot','not','neg','plus','inc','dec','inc_loc','dec_loc','add_loc','post_inc','post_dec','strict_eq','strict_neq','eq','neq','lt','lte','gt','gte','is_null','is_undefined','is_undefined_or_null','typeof_is_undefined','typeof_is_function','instanceof','typeof','pow'): return 'arith/compare'
    if op.startswith('for_') or op.startswith('iterator'): return 'iteration'
    return 'other'
f=sys.argv[1]; L=open(f).read().split('\n'); hdr=L[0]
d={}
for l in L[1:]:
    if not l: continue
    op,n=l.split(); d[op]=int(n)
t=sum(d.values()); c={}
for op,n in d.items(): c[cat(op)]=c.get(cat(op),0)+n
m=re.match(r'#calls (\d+) bytecode_calls (\d+)',hdr); calls,bc=int(m[1]),int(m[2])
print(f.split('/')[-1], 'ops=%d'%t, 'JS_CallInternal entries=%d bytecode=%d ops/bytecode-call=%.1f'%(calls,bc,t/bc))
for k,v in sorted(c.items(), key=lambda x:-x[1]): print('  %-18s %5.1f%%'%(k,100*v/t))
print('  others list:', sorted([(op,n) for op,n in d.items() if cat(op)=='other'], key=lambda x:-x[1])[:8])
