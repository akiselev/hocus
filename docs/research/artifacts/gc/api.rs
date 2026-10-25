//! Type-level prototype of the proposed hocus GC API. Safe Rust only: the heap is a Vec so the
//! borrow rules can be checked without the real allocator. `Gc` = index, `Local` = handle slot.
#![forbid(unsafe_code)]
use std::cell::{Cell, RefCell};
use std::marker::PhantomData;

#[derive(Clone, Copy, Debug, PartialEq)]
enum Raw { Undef, Int(i32), Obj(u32) }

/// Unrooted value, valid only while the `NoGc` borrow `'n` lives (gc-arena-style brand).
#[derive(Clone, Copy, Debug)]
pub struct Value<'n> { raw: Raw, _n: PhantomData<&'n NoGc> }
#[derive(Clone, Copy)]
pub struct Obj<'n> { idx: u32, _n: PhantomData<&'n NoGc> }
/// Rooted handle: an index into the handle stack. Lifetime = the handle scope, not a borrow.
#[derive(Clone, Copy)]
pub struct Local<'s> { slot: usize, _s: PhantomData<*mut &'s ()> } // !Send, invariant-ish

pub struct NoGc { _p: () }
struct ObjData { proto: Cell<Raw>, slots: RefCell<Vec<Cell<Raw>>> }
pub struct Agent { objs: Vec<ObjData>, handles: RefCell<Vec<Raw>>, frames: Vec<Raw>, token: NoGc, gc_count: u32 }
pub struct Cx<'s> { agent: &'s mut Agent }

impl Agent {
    pub fn new() -> Self { Agent { objs: vec![], handles: RefCell::new(vec![]), frames: vec![], token: NoGc { _p: () }, gc_count: 0 } }
    /// Entry: a fresh handle scope with a generative lifetime.
    pub fn enter<R>(&mut self, f: impl for<'s> FnOnce(&mut Cx<'s>) -> R) -> R {
        let base = self.handles.borrow().len();
        let r = f(&mut Cx { agent: self });
        self.handles.borrow_mut().truncate(base);
        r
    }
}
impl<'s> Cx<'s> {
    /// Shared token: while any `&NoGc` (and so any `Value<'n>`) lives, `&mut self` is unavailable,
    /// so no allocation and no GC can happen.
    pub fn nogc(&self) -> &NoGc { &self.agent.token }
    /// Root an unrooted value. Takes `&self`, so it works while a `NoGc` borrow is live.
    pub fn root(&self, v: Value<'_>) -> Local<'s> {
        let mut h = self.agent.handles.borrow_mut();
        h.push(v.raw);
        Local { slot: h.len() - 1, _s: PhantomData }
    }
    pub fn get<'n>(&'n self, l: Local<'s>) -> Value<'n> {
        Value { raw: self.agent.handles.borrow()[l.slot], _n: PhantomData }
    }
    /// May collect: needs `&mut self`.
    pub fn alloc_object(&mut self, proto: Local<'s>) -> Local<'s> {
        self.maybe_gc();
        let p = self.agent.handles.borrow()[proto.slot];
        self.agent.objs.push(ObjData { proto: Cell::new(p), slots: RefCell::new(vec![]) });
        let raw = Raw::Obj(self.agent.objs.len() as u32 - 1);
        let mut h = self.agent.handles.borrow_mut();
        h.push(raw);
        Local { slot: h.len() - 1, _s: PhantomData }
    }
    fn maybe_gc(&mut self) { self.agent.gc_count += 1; /* a moving GC would rewrite handles+frames here */ }
}
impl<'n> Value<'n> {
    pub fn as_obj(self) -> Option<Obj<'n>> { if let Raw::Obj(i) = self.raw { Some(Obj { idx: i, _n: PhantomData }) } else { None } }
    pub fn int(i: i32) -> Self { Value { raw: Raw::Int(i), _n: PhantomData } }
}
impl<'n> Obj<'n> {
    /// Field write through the shared token: Cell semantics + the one write barrier funnel.
    pub fn push_slot(self, cx: &'n Cx<'_>, v: Value<'n>) { cx.agent.objs[self.idx as usize].slots.borrow_mut().push(Cell::new(v.raw)); }
    pub fn slot(self, cx: &'n Cx<'_>, i: usize) -> Value<'n> { Value { raw: cx.agent.objs[self.idx as usize].slots.borrow()[i].get(), _n: PhantomData } }
}

/// A builtin that may call back into JS (and so GC) takes rooted args.
fn may_call_js<'s>(cx: &mut Cx<'s>, a: Local<'s>) -> Local<'s> { cx.alloc_object(a) }

fn main() {
    let mut agent = Agent::new();
    agent.enter(|cx| {
        let undef = cx.root(Value { raw: Raw::Undef, _n: PhantomData });
        let o = cx.alloc_object(undef);
        // NoGc region: zero-cost unrooted values.
        {
            let obj = cx.get(o).as_obj().unwrap();
            obj.push_slot(cx, Value::int(7));
            let _seven = obj.slot(cx, 0);
        }
        let r = may_call_js(cx, o);        // GC may run; `o` and `r` stay valid (rooted)
        let v = cx.get(r);                 // unrooted read
        let rv = cx.root(v);               // root it before the next GC point
        let _ = may_call_js(cx, rv);
        #[cfg(fail_use_after_gc)]
        { let v2 = cx.get(o); let _ = may_call_js(cx, rv); let _ = v2.as_obj(); }
        #[cfg(fail_escape)]
        { let mut out = None; cx.nogc(); out = Some(cx.get(o)); drop(out); }
    });
    #[cfg(fail_local_escapes_scope)]
    { let l = agent.enter(|cx| cx.root(Value::int(1))); let _ = l; }
    println!("ok, gc points = {}", agent.gc_count);
}
