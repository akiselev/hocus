// Toy register VM: three dispatch strategies over the same program.
// Program: r0 = n; r1 = 0; r2 = 0; loop { r2 = r2 + r0; r1 = r1 ^ r2; r0 = r0 - 1; if r0 != 0 goto loop }; ret r1+r2
use std::time::Instant;

#[derive(Clone, Copy)]
enum Op { AddRR{d:u8,a:u8,b:u8}, XorRR{d:u8,a:u8,b:u8}, SubRI{d:u8,a:u8,i:i32}, Jnz{r:u8,off:i32}, LoadI{d:u8,i:i32}, Mov{d:u8,a:u8}, AddI{d:u8,a:u8,i:i32}, Ret{a:u8,b:u8} }

fn program() -> Vec<Op> {
    use Op::*;
    vec![ LoadI{d:1,i:0}, LoadI{d:2,i:0},
          AddRR{d:2,a:2,b:0}, Mov{d:3,a:2}, XorRR{d:1,a:1,b:3}, AddI{d:4,a:1,i:7}, SubRI{d:0,a:0,i:1}, Jnz{r:0,off:-5},
          Ret{a:1,b:2} ]
}

// 1. match loop
#[inline(never)]
fn run_match(code: &[Op], n: i64) -> i64 {
    let mut r = [0i64; 8];
    r[0] = n;
    let mut pc = 0usize;
    loop {
        match code[pc] {
            Op::AddRR{d,a,b} => { r[d as usize & 7] = r[a as usize & 7].wrapping_add(r[b as usize & 7]); pc += 1; }
            Op::XorRR{d,a,b} => { r[d as usize & 7] = r[a as usize & 7] ^ r[b as usize & 7]; pc += 1; }
            Op::SubRI{d,a,i} => { r[d as usize & 7] = r[a as usize & 7].wrapping_sub(i as i64); pc += 1; }
            Op::AddI{d,a,i} => { r[d as usize & 7] = r[a as usize & 7].wrapping_add(i as i64); pc += 1; }
            Op::Mov{d,a} => { r[d as usize & 7] = r[a as usize & 7]; pc += 1; }
            Op::LoadI{d,i} => { r[d as usize & 7] = i as i64; pc += 1; }
            Op::Jnz{r:x,off} => { if r[x as usize & 7] != 0 { pc = (pc as isize + off as isize) as usize; } else { pc += 1; } }
            Op::Ret{a,b} => return r[a as usize & 7].wrapping_add(r[b as usize & 7]),
        }
    }
}

// 2. call loop: table of fn pointers, each returns the next pc (or usize::MAX)
type H = fn(&Op, &mut [i64; 8], usize) -> usize;
fn h_add(o:&Op, r:&mut [i64;8], pc:usize)->usize{ if let Op::AddRR{d,a,b}=*o { r[d as usize&7]=r[a as usize&7].wrapping_add(r[b as usize&7]); } pc+1 }
fn h_xor(o:&Op, r:&mut [i64;8], pc:usize)->usize{ if let Op::XorRR{d,a,b}=*o { r[d as usize&7]=r[a as usize&7]^r[b as usize&7]; } pc+1 }
fn h_sub(o:&Op, r:&mut [i64;8], pc:usize)->usize{ if let Op::SubRI{d,a,i}=*o { r[d as usize&7]=r[a as usize&7].wrapping_sub(i as i64); } pc+1 }
fn h_addi(o:&Op, r:&mut [i64;8], pc:usize)->usize{ if let Op::AddI{d,a,i}=*o { r[d as usize&7]=r[a as usize&7].wrapping_add(i as i64); } pc+1 }
fn h_mov(o:&Op, r:&mut [i64;8], pc:usize)->usize{ if let Op::Mov{d,a}=*o { r[d as usize&7]=r[a as usize&7]; } pc+1 }
fn h_li(o:&Op, r:&mut [i64;8], pc:usize)->usize{ if let Op::LoadI{d,i}=*o { r[d as usize&7]=i as i64; } pc+1 }
fn h_jnz(o:&Op, r:&mut [i64;8], pc:usize)->usize{ if let Op::Jnz{r:x,off}=*o { if r[x as usize&7]!=0 { return (pc as isize+off as isize) as usize; } } pc+1 }
fn h_ret(_o:&Op, _r:&mut [i64;8], _pc:usize)->usize{ usize::MAX }
fn handler_of(o:&Op)->H { match o { Op::AddRR{..}=>h_add, Op::XorRR{..}=>h_xor, Op::SubRI{..}=>h_sub, Op::AddI{..}=>h_addi, Op::Mov{..}=>h_mov, Op::LoadI{..}=>h_li, Op::Jnz{..}=>h_jnz, Op::Ret{..}=>h_ret } }
#[inline(never)]
fn run_call(code: &[Op], hs: &[H], n: i64) -> i64 {
    let mut r = [0i64; 8]; r[0] = n; let mut pc = 0usize;
    loop { let nx = hs[pc](&code[pc], &mut r, pc); if nx == usize::MAX { let Op::Ret{a,b}=code[pc] else {unreachable!()}; return r[a as usize&7].wrapping_add(r[b as usize&7]); } pc = nx; }
}

// 3. direct-threaded tail calls (stable Rust; relies on LLVM sibling-call optimisation)
#[derive(Clone, Copy)]
struct TI { h: TH, d: u8, a: u8, b: u8, i: i32 }
type TH = fn(ip: *const TI, r: &mut [i64; 8]) -> i64;
macro_rules! next { ($ip:expr, $r:expr) => {{ let ip = $ip; unsafe { ((*ip).h)(ip, $r) } }} }
fn t_add(ip:*const TI, r:&mut [i64;8])->i64{ let x=unsafe{*ip}; r[x.d as usize&7]=r[x.a as usize&7].wrapping_add(r[x.b as usize&7]); next!(unsafe{ip.add(1)}, r) }
fn t_xor(ip:*const TI, r:&mut [i64;8])->i64{ let x=unsafe{*ip}; r[x.d as usize&7]=r[x.a as usize&7]^r[x.b as usize&7]; next!(unsafe{ip.add(1)}, r) }
fn t_sub(ip:*const TI, r:&mut [i64;8])->i64{ let x=unsafe{*ip}; r[x.d as usize&7]=r[x.a as usize&7].wrapping_sub(x.i as i64); next!(unsafe{ip.add(1)}, r) }
fn t_addi(ip:*const TI, r:&mut [i64;8])->i64{ let x=unsafe{*ip}; r[x.d as usize&7]=r[x.a as usize&7].wrapping_add(x.i as i64); next!(unsafe{ip.add(1)}, r) }
fn t_mov(ip:*const TI, r:&mut [i64;8])->i64{ let x=unsafe{*ip}; r[x.d as usize&7]=r[x.a as usize&7]; next!(unsafe{ip.add(1)}, r) }
fn t_li(ip:*const TI, r:&mut [i64;8])->i64{ let x=unsafe{*ip}; r[x.d as usize&7]=x.i as i64; next!(unsafe{ip.add(1)}, r) }
fn t_jnz(ip:*const TI, r:&mut [i64;8])->i64{ let x=unsafe{*ip}; let n = if r[x.a as usize&7]!=0 { unsafe{ip.offset(x.i as isize)} } else { unsafe{ip.add(1)} }; next!(n, r) }
fn t_ret(ip:*const TI, r:&mut [i64;8])->i64{ let x=unsafe{*ip}; r[x.a as usize&7].wrapping_add(r[x.b as usize&7]) }
fn thread(code:&[Op])->Vec<TI>{ code.iter().map(|o| match *o {
    Op::AddRR{d,a,b}=>TI{h:t_add,d,a,b,i:0}, Op::XorRR{d,a,b}=>TI{h:t_xor,d,a,b,i:0},
    Op::SubRI{d,a,i}=>TI{h:t_sub,d,a,b:0,i}, Op::AddI{d,a,i}=>TI{h:t_addi,d,a,b:0,i},
    Op::Mov{d,a}=>TI{h:t_mov,d,a,b:0,i:0}, Op::LoadI{d,i}=>TI{h:t_li,d,a:0,b:0,i},
    Op::Jnz{r,off}=>TI{h:t_jnz,d:0,a:r,b:0,i:off}, Op::Ret{a,b}=>TI{h:t_ret,d:0,a,b,i:0} }).collect() }
#[inline(never)]
fn run_tail(t:&[TI], n:i64)->i64{ let mut r=[0i64;8]; r[0]=n; next!(t.as_ptr(), &mut r) }

fn main() {
    let n: i64 = std::env::args().nth(1).map(|s| s.parse().unwrap()).unwrap_or(100_000_000);
    let code = program();
    let hs: Vec<H> = code.iter().map(handler_of).collect();
    let t = thread(&code);
    for round in 0..3 {
        let s = Instant::now(); let a = run_match(&code, n); let tm = s.elapsed();
        let s = Instant::now(); let b = run_call(&code, &hs, n); let tc = s.elapsed();
        let s = Instant::now(); let c = run_tail(&t, n); let tt = s.elapsed();
        assert!(a == b && b == c);
        let ops = (n as f64) * 6.0;
        println!("round {round}: match {:.2} ns/op  call-loop {:.2} ns/op  tail-thread {:.2} ns/op  (chk {a})",
            tm.as_nanos() as f64 / ops, tc.as_nanos() as f64 / ops, tt.as_nanos() as f64 / ops);
    }
}
