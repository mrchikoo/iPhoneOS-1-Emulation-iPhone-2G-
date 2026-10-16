  ;; ======================================================================
  ;; emu-net additions (inserted by patch.py).  Appended after the last
  ;; original function, so no original function index changes.
  ;;
  ;; Everything lives in ONE block obtained lazily from the module's own
  ;; malloc (original func 16, export "V") on first use.  It is NOT part of
  ;; the machine struct (address 23560..), so snapshots / seed-JIT ABI are
  ;; unaffected.  Layout (offsets from $net_base):
  ;;   0     header (64 B):  +0 magic 'NET1'  +4 txhead +8 txtail +12 rxhead
  ;;                         +16 rxtail +20 trace head +24 trace tail
  ;;                         +28 tx drops +32 rx drops +36 irq pending
  ;;                         +40 trace seq +44 sdio mode (0 log-only, 1 readback)
  ;;                         +48 reg reads +52 reg writes
  ;;   64    SDIO register file, 4096 B (byte addressed)
  ;;   4160  trace ring: 1024 entries x 16 B
  ;;         {u32 rw<<31|size<<16|off, u32 value, u32 seq, u32 extra repeats}
  ;;   20544 TX ring (guest->host): 128 slots x 2048 B {u32 len, data[<=2044]}
  ;;   282688 RX ring (host->guest): same
  ;;   total 544832 B
  ;; Ring counters are free-running u32s; count = head - tail; slot = idx&127.
  ;; ======================================================================
  ;; ($net_base global is inserted by patch.py AFTER the original global 0)

  (func $net_init (result i32)
    (local $p i32)
    global.get $net_base
    if (result i32)
      i32.const 1
    else
      i32.const 544832
      call 16
      local.tee $p
      if (result i32)
        local.get $p
        i32.const 0
        i32.const 544832
        memory.fill
        local.get $p
        i32.const 0x3154454e
        i32.store
        local.get $p
        i32.const 2
        i32.store offset=44
        local.get $p
        global.set $net_base
        i32.const 1
      else
        i32.const 0
      end
    end)

  ;; ring_push(ring_off, head_hdr_off, tail_hdr_off, src, len) -> 1 ok / 0 full|bad|nomem
  (func $ring_push (param $ring i32) (param $ho i32) (param $to i32) (param $src i32) (param $len i32) (result i32)
    (local $h i32) (local $slot i32)
    call $net_init
    i32.eqz
    if
      i32.const 0
      return
    end
    local.get $len
    i32.eqz
    local.get $len
    i32.const 2044
    i32.gt_u
    i32.or
    if
      i32.const 0
      return
    end
    global.get $net_base
    local.get $ho
    i32.add
    i32.load
    local.tee $h
    global.get $net_base
    local.get $to
    i32.add
    i32.load
    i32.sub
    i32.const 128
    i32.ge_u
    if
      i32.const 0
      return
    end
    global.get $net_base
    local.get $ring
    i32.add
    local.get $h
    i32.const 127
    i32.and
    i32.const 11
    i32.shl
    i32.add
    local.tee $slot
    local.get $len
    i32.store
    local.get $slot
    i32.const 4
    i32.add
    local.get $src
    local.get $len
    memory.copy
    global.get $net_base
    local.get $ho
    i32.add
    local.get $h
    i32.const 1
    i32.add
    i32.store
    i32.const 1)

  ;; ring_pop(ring_off, head_hdr_off, tail_hdr_off, dst, max) -> len, 0 empty, -1 dst too small (frame kept)
  (func $ring_pop (param $ring i32) (param $ho i32) (param $to i32) (param $dst i32) (param $max i32) (result i32)
    (local $t i32) (local $slot i32) (local $len i32)
    global.get $net_base
    i32.eqz
    if
      i32.const 0
      return
    end
    global.get $net_base
    local.get $to
    i32.add
    i32.load
    local.set $t
    global.get $net_base
    local.get $ho
    i32.add
    i32.load
    local.get $t
    i32.eq
    if
      i32.const 0
      return
    end
    global.get $net_base
    local.get $ring
    i32.add
    local.get $t
    i32.const 127
    i32.and
    i32.const 11
    i32.shl
    i32.add
    local.tee $slot
    i32.load
    local.tee $len
    local.get $max
    i32.gt_u
    if
      i32.const -1
      return
    end
    local.get $dst
    local.get $slot
    i32.const 4
    i32.add
    local.get $len
    memory.copy
    global.get $net_base
    local.get $to
    i32.add
    local.get $t
    i32.const 1
    i32.add
    i32.store
    local.get $len)

  ;; ---- exports -------------------------------------------------------
  (func $emu_net_tx_pop (param $ptr i32) (param $max i32) (result i32)
    i32.const 20544
    i32.const 4
    i32.const 8
    local.get $ptr
    local.get $max
    call $ring_pop)

  ;; device-side producer for guest->host frames (future SDIO model calls
  ;; this; also exported for tests).  Counts drops.
  (func $emu_net_tx_push (param $ptr i32) (param $len i32) (result i32)
    (local $ok i32)
    i32.const 20544
    i32.const 4
    i32.const 8
    local.get $ptr
    local.get $len
    call $ring_push
    local.tee $ok
    i32.eqz
    if
      global.get $net_base
      if
        global.get $net_base
        global.get $net_base
        i32.load offset=28
        i32.const 1
        i32.add
        i32.store offset=28
      end
    end
    local.get $ok)

  (func $emu_net_rx_push (param $ptr i32) (param $len i32) (result i32)
    (local $ok i32)
    i32.const 282688
    i32.const 12
    i32.const 16
    local.get $ptr
    local.get $len
    call $ring_push
    local.tee $ok
    ;; model mode: let the card start an upload (RxPD packet + card interrupt)
    (if (i32.and (i32.ne (local.get $ok) (i32.const 0)) (i32.eq (i32.load offset=44 (global.get $net_base)) (i32.const 2)))
      (then (drop (call $emu_net_card_kick))))
    i32.eqz
    if
      global.get $net_base
      if
        global.get $net_base
        global.get $net_base
        i32.load offset=32
        i32.const 1
        i32.add
        i32.store offset=32
      end
    end
    local.get $ok)

  ;; device-side consumer for host->guest frames
  (func $emu_net_rx_pop (param $ptr i32) (param $max i32) (result i32)
    i32.const 282688
    i32.const 12
    i32.const 16
    local.get $ptr
    local.get $max
    call $ring_pop)

  ;; emu_net_state(idx): 0 base ptr (allocates), 1 tx queued, 2 rx queued,
  ;; 3 tx drops, 4 rx drops, 5 trace entries queued, 6 irq pending,
  ;; 7 sdio mode, 8 reg reads, 9 reg writes, 10 version(1).  else -1.
  (func $emu_net_state (param $idx i32) (result i32)
    (local $b i32)
    local.get $idx
    i32.const 10
    i32.eq
    if
      i32.const 1
      return
    end
    call $net_init
    i32.eqz
    if
      i32.const -1
      return
    end
    global.get $net_base
    local.set $b
    local.get $idx
    i32.eqz
    if
      local.get $b
      return
    end
    local.get $idx
    i32.const 1
    i32.eq
    if
      local.get $b
      i32.load offset=4
      local.get $b
      i32.load offset=8
      i32.sub
      return
    end
    local.get $idx
    i32.const 2
    i32.eq
    if
      local.get $b
      i32.load offset=12
      local.get $b
      i32.load offset=16
      i32.sub
      return
    end
    local.get $idx
    i32.const 3
    i32.eq
    if
      local.get $b
      i32.load offset=28
      return
    end
    local.get $idx
    i32.const 4
    i32.eq
    if
      local.get $b
      i32.load offset=32
      return
    end
    local.get $idx
    i32.const 5
    i32.eq
    if
      local.get $b
      i32.load offset=20
      local.get $b
      i32.load offset=24
      i32.sub
      return
    end
    local.get $idx
    i32.const 6
    i32.eq
    if
      local.get $b
      i32.load offset=36
      return
    end
    local.get $idx
    i32.const 7
    i32.eq
    if
      local.get $b
      i32.load offset=44
      return
    end
    local.get $idx
    i32.const 8
    i32.eq
    if
      local.get $b
      i32.load offset=48
      return
    end
    local.get $idx
    i32.const 9
    i32.eq
    if
      local.get $b
      i32.load offset=52
      return
    end
    i32.const -1)

  ;; emu_net_sdio_mode(m) -> previous mode.  0 = trace only (reads return 0
  ;; exactly like the unpatched core), 1 = register-file readback.
  (func $emu_net_sdio_mode (param $m i32) (result i32)
    call $net_init
    i32.eqz
    if
      i32.const -1
      return
    end
    global.get $net_base
    i32.load offset=44
    global.get $net_base
    local.get $m
    i32.store offset=44)

  ;; emu_net_trace_pop(dst, max_entries) -> entries copied (16 B each)
  (func $emu_net_trace_pop (param $dst i32) (param $max i32) (result i32)
    (local $t i32) (local $n i32) (local $i i32)
    global.get $net_base
    i32.eqz
    if
      i32.const 0
      return
    end
    global.get $net_base
    i32.load offset=24
    local.set $t
    global.get $net_base
    i32.load offset=20
    local.get $t
    i32.sub
    local.tee $n
    local.get $max
    i32.gt_u
    if
      local.get $max
      local.set $n
    end
    loop $copy
      local.get $i
      local.get $n
      i32.lt_u
      if
        local.get $dst
        local.get $i
        i32.const 4
        i32.shl
        i32.add
        global.get $net_base
        i32.const 4160
        i32.add
        local.get $t
        local.get $i
        i32.add
        i32.const 1023
        i32.and
        i32.const 4
        i32.shl
        i32.add
        i32.const 16
        memory.copy
        local.get $i
        i32.const 1
        i32.add
        local.set $i
        br $copy
      end
    end
    global.get $net_base
    local.get $t
    local.get $n
    i32.add
    i32.store offset=24
    local.get $n)

  ;; ---- SDIO register file --------------------------------------------
  ;; Appends {w0=rw<<31|size<<16|off, val, seq, repeats}.  An access identical
  ;; to the newest unread entry (same w0 and value: status-register polling)
  ;; only bumps that entry's repeat word (+12), so busy-wait loops cannot flood
  ;; the ring.  When 1024 unread entries accumulate the oldest is dropped.
  (func $sdio_trace (param $off i32) (param $size i32) (param $rw i32) (param $val i32)
    (local $h i32) (local $e i32) (local $b i32) (local $w0 i32)
    global.get $net_base
    local.set $b
    local.get $rw
    i32.const 31
    i32.shl
    local.get $size
    i32.const 16
    i32.shl
    i32.or
    local.get $off
    i32.or
    local.set $w0
    local.get $b
    i32.load offset=20
    local.set $h
    local.get $h
    local.get $b
    i32.load offset=24
    i32.ne
    if
      local.get $b
      i32.const 4160
      i32.add
      local.get $h
      i32.const 1
      i32.sub
      i32.const 1023
      i32.and
      i32.const 4
      i32.shl
      i32.add
      local.tee $e
      i32.load
      local.get $w0
      i32.eq
      local.get $e
      i32.load offset=4
      local.get $val
      i32.eq
      i32.and
      if
        local.get $e
        local.get $e
        i32.load offset=12
        i32.const 1
        i32.add
        i32.store offset=12
        return
      end
    end
    local.get $b
    i32.const 4160
    i32.add
    local.get $h
    i32.const 1023
    i32.and
    i32.const 4
    i32.shl
    i32.add
    local.tee $e
    local.get $w0
    i32.store
    local.get $e
    local.get $val
    i32.store offset=4
    local.get $e
    local.get $b
    i32.load offset=40
    i32.const 1
    i32.add
    local.tee $rw
    i32.store offset=8
    local.get $b
    local.get $rw
    i32.store offset=40
    local.get $e
    i32.const 0
    i32.store offset=12
    local.get $b
    local.get $h
    i32.const 1
    i32.add
    local.tee $h
    i32.store offset=20
    local.get $h
    local.get $b
    i32.load offset=24
    i32.sub
    i32.const 1024
    i32.gt_u
    if
      local.get $b
      local.get $h
      i32.const 1024
      i32.sub
      i32.store offset=24
    end)

  ;; Hooks for later real behaviour.  read hook may replace the value the
  ;; guest sees; write hook runs after the byte store and may update regs,
  ;; push frames via $emu_net_tx_push, pop frames via $emu_net_rx_pop, or
  ;; set the IRQ-pending word (header +36).
  ;; mode 2 (default) = SDIO host + Marvell card model (sdio_model.wat)
  (func $sdio_read_hook (param $off i32) (param $size i32) (param $val i32) (result i32)
    (if (i32.eq (i32.load offset=44 (global.get $net_base)) (i32.const 2))
      (then (return (call $model_read (local.get $off) (local.get $size) (local.get $val)))))
    local.get $val)
  (func $sdio_write_hook (param $off i32) (param $size i32) (param $val i32)
    (if (i32.eq (i32.load offset=44 (global.get $net_base)) (i32.const 2))
      (then (call $model_write (local.get $off) (local.get $size) (local.get $val)))))

  ;; little-endian byte-wise load/store in the register file
  (func $regs_load (param $off i32) (param $size i32) (result i32)
    (local $v i32) (local $i i32) (local $a i32)
    loop $l
      local.get $i
      local.get $size
      i32.lt_u
      if
        local.get $off
        local.get $i
        i32.add
        local.tee $a
        i32.const 4096
        i32.lt_u
        if
          global.get $net_base
          local.get $a
          i32.add
          i32.load8_u offset=64
          local.get $i
          i32.const 3
          i32.shl
          i32.shl
          local.get $v
          i32.or
          local.set $v
        end
        local.get $i
        i32.const 1
        i32.add
        local.set $i
        br $l
      end
    end
    local.get $v)

  (func $regs_store (param $off i32) (param $size i32) (param $val i32)
    (local $i i32) (local $a i32)
    loop $l
      local.get $i
      local.get $size
      i32.lt_u
      if
        local.get $off
        local.get $i
        i32.add
        local.tee $a
        i32.const 4096
        i32.lt_u
        if
          global.get $net_base
          local.get $a
          i32.add
          local.get $val
          local.get $i
          i32.const 3
          i32.shl
          i32.shr_u
          i32.store8 offset=64
        end
        local.get $i
        i32.const 1
        i32.add
        local.set $i
        br $l
      end
    end)

  ;; off = physical address - 0x38D00000 (0..4095); size 1/2/4 (other sizes: 0)
  (func $sdio_read (param $off i32) (param $size i32) (result i32)
    (local $v i32)
    call $net_init
    i32.eqz
    if
      i32.const 0
      return
    end
    global.get $net_base
    global.get $net_base
    i32.load offset=48
    i32.const 1
    i32.add
    i32.store offset=48
    ;; mode 0: log only, value 0 (matches the unpatched "unmapped" behaviour)
    global.get $net_base
    i32.load offset=44
    if
      local.get $off
      local.get $size
      call $regs_load
      local.set $v
    end
    local.get $off
    local.get $size
    local.get $v
    call $sdio_read_hook
    local.set $v
    local.get $off
    local.get $size
    i32.const 0
    local.get $v
    call $sdio_trace
    local.get $v)

  (func $sdio_write (param $off i32) (param $size i32) (param $val i32)
    call $net_init
    i32.eqz
    if
      return
    end
    global.get $net_base
    global.get $net_base
    i32.load offset=52
    i32.const 1
    i32.add
    i32.store offset=52
    local.get $off
    local.get $size
    local.get $val
    call $regs_store
    local.get $off
    local.get $size
    local.get $val
    call $sdio_trace_w
    local.get $off
    local.get $size
    local.get $val
    call $sdio_write_hook)
  (func $sdio_trace_w (param $off i32) (param $size i32) (param $val i32)
    local.get $off
    local.get $size
    i32.const 1
    local.get $val
    call $sdio_trace)

  ;; host-side poke/peek of the register file through the exact same path the
  ;; guest uses (used by test.mjs and for debugging).
  (func $emu_net_sdio_read (param $off i32) (param $size i32) (result i32)
    local.get $off
    i32.const 4095
    i32.and
    local.get $size
    call $sdio_read)
  (func $emu_net_sdio_write (param $off i32) (param $size i32) (param $val i32)
    local.get $off
    i32.const 4095
    i32.and
    local.get $size
    local.get $val
    call $sdio_write)
