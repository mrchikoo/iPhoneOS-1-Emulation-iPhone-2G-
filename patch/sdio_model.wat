  ;; ======================================================================
  ;; SDIO host controller (S5L8900, per re-disasm/SDIO_MAP.md) + Marvell
  ;; 88W8686 card model.  Active when sdio mode == 2 (the default set by
  ;; $net_init).  Mode 0/1 keep the old log-only / readback behaviour.
  ;;
  ;; Card block ($c_card, malloc'd lazily, 0x2C000 bytes):
  ;;   0x00000 fn0 space (CCCR + FBR), 0x800 B
  ;;   0x00800 fn1 registers, 0x100 B
  ;;   0x00900 event log: 256 x u32 {kind<<24 | value}  (head = $c_logh)
  ;;   0x01000 current upload buffer (card->host), 4096 B
  ;;   0x02000 helper capture, 4096 B
  ;;   0x03000 firmware capture, 0x20000 B
  ;;   0x23000 upload queue, 16 slots x 2048 B {u16 len, u16 type, payload}
  ;;   0x2B000 scratch buffer 4096 B
  ;; Guest RAM: machine struct 23560: +67644 host ptr, +67648 phys base,
  ;; +67652 size  (absolute 91204 / 91208 / 91212).
  ;; ======================================================================
  (global $c_card (mut i32) (i32.const 0))
  (global $m_dsta (mut i32) (i32.const 0))
  (global $m_irq (mut i32) (i32.const 0))
  (global $m_irqmask (mut i32) (i32.const 0))
  (global $m_line (mut i32) (i32.const 0))
  (global $m_raises (mut i32) (i32.const 0))
  (global $x_pend (mut i32) (i32.const 0))
  (global $x_write (mut i32) (i32.const 0))
  (global $x_fn (mut i32) (i32.const 0))
  (global $x_addr (mut i32) (i32.const 0))
  (global $fw_stage (mut i32) (i32.const 0))
  (global $fw_hlen (mut i32) (i32.const 0))
  (global $fw_len (mut i32) (i32.const 0))
  (global $fw_req (mut i32) (i32.const 0))
  (global $scratch (mut i32) (i32.const 0))
  (global $hint (mut i32) (i32.const 0))
  (global $c_level (mut i32) (i32.const 0))
  (global $up_len (mut i32) (i32.const 0))
  (global $q_head (mut i32) (i32.const 0))
  (global $q_tail (mut i32) (i32.const 0))
  (global $c_logh (mut i32) (i32.const 0))
  (global $c_resets (mut i32) (i32.const 0))
  (global $c_cmds (mut i32) (i32.const 0))
  (global $c_dmas (mut i32) (i32.const 0))
  (global $c_assoc (mut i32) (i32.const 0))
  (global $c_sleep (mut i32) (i32.const 0))

  (func $card_init (result i32)
    (local $p i32)
    (if (global.get $c_card) (then (return (i32.const 1))))
    (local.set $p (call 16 (i32.const 0x2C000)))
    (if (i32.eqz (local.get $p)) (then (return (i32.const 0))))
    (memory.fill (local.get $p) (i32.const 0) (i32.const 0x2C000))
    (global.set $c_card (local.get $p))
    (call $card_reset)
    (i32.const 1))

  (func $clog (param $kind i32) (param $v i32)
    (i32.store offset=0x900
      (i32.add (global.get $c_card) (i32.shl (i32.and (global.get $c_logh) (i32.const 255)) (i32.const 2)))
      (i32.or (i32.shl (local.get $kind) (i32.const 24)) (i32.and (local.get $v) (i32.const 0xFFFFFF))))
    (global.set $c_logh (i32.add (global.get $c_logh) (i32.const 1))))

  (func $card_reset
    (local $c i32)
    (local.set $c (global.get $c_card))
    (memory.fill (local.get $c) (i32.const 0) (i32.const 0x900))
    ;; CCCR: SDIO rev 2.0 / CCCR 1.20, SD 2.0, caps SMB|S4MI, CIS 0x001000
    (i32.store8 offset=0x00 (local.get $c) (i32.const 0x32))
    (i32.store8 offset=0x01 (local.get $c) (i32.const 0x02))
    (i32.store8 offset=0x08 (local.get $c) (i32.const 0x12))
    (i32.store8 offset=0x0A (local.get $c) (i32.const 0x10))
    ;; FBR1: standard interface code 7 (WLAN), CIS 0x002000
    (i32.store8 offset=0x100 (local.get $c) (i32.const 0x07))
    (i32.store8 offset=0x10A (local.get $c) (i32.const 0x20))
    ;; fn1 IO port = 0x010000
    (i32.store8 offset=0x802 (local.get $c) (i32.const 0x01))
    (global.set $fw_stage (i32.const 0))
    (global.set $fw_hlen (i32.const 0))
    (global.set $fw_len (i32.const 0))
    (global.set $fw_req (i32.const 0))
    (global.set $scratch (i32.const 0))
    (global.set $hint (i32.const 0))
    (global.set $c_level (i32.const 0))
    (global.set $up_len (i32.const 0))
    (global.set $q_head (i32.const 0))
    (global.set $q_tail (i32.const 0))
    (global.set $x_pend (i32.const 0))
    (global.set $c_assoc (i32.const 0))
    (global.set $c_sleep (i32.const 0))
    (global.set $c_resets (i32.add (global.get $c_resets) (i32.const 1))))

  ;; guest physical -> wasm address (0 if [phys, phys+len) is not RAM)
  (func $gptr (param $phys i32) (param $len i32) (result i32)
    (local $off i32) (local $size i32)
    (local.set $off (i32.sub (local.get $phys) (i32.load (i32.const 91208))))
    (local.set $size (i32.load (i32.const 91212)))
    (if (i32.lt_u (local.get $phys) (i32.load (i32.const 91208))) (then (return (i32.const 0))))
    (if (i32.gt_u (local.get $off) (local.get $size)) (then (return (i32.const 0))))
    (if (i32.gt_u (local.get $len) (i32.sub (local.get $size) (local.get $off))) (then (return (i32.const 0))))
    (i32.add (i32.load (i32.const 91204)) (local.get $off)))

  ;; ---- interrupt plumbing ---------------------------------------------
  (func $host_update_line
    (local $l i32)
    (local.set $l (i32.ne (i32.and (global.get $m_irq) (global.get $m_irqmask)) (i32.const 0)))
    (if (i32.ne (local.get $l) (global.get $m_line))
      (then
        (global.set $m_line (local.get $l))
        (if (local.get $l)
          (then (global.set $m_raises (i32.add (global.get $m_raises) (i32.const 1)))
                (call $sdio_raise_irq))
          (else (call $sdio_lower_irq))))))

  ;; card interrupt (DAT1) level; a rising edge latches host IRQ bit1.
  ;; $retrig: also latch when the level stays high (after a partial ack).
  (func $card_update_irq (param $retrig i32)
    (local $l i32) (local $c i32)
    (local.set $c (global.get $c_card))
    (local.set $l
      (i32.and
        (i32.ne (i32.and (i32.and (global.get $hint) (i32.load8_u offset=0x804 (local.get $c))) (i32.const 3)) (i32.const 0))
        (i32.eq (i32.and (i32.load8_u offset=0x04 (local.get $c)) (i32.const 3)) (i32.const 3))))
    (if (i32.and (local.get $l) (i32.or (i32.eqz (global.get $c_level)) (local.get $retrig)))
      (then (global.set $m_irq (i32.or (global.get $m_irq) (i32.const 2)))))
    (global.set $c_level (local.get $l))
    (call $host_update_line))

  ;; ---- card register space (CMD52) -----------------------------------
  (func $card_read (param $fn i32) (param $a i32) (result i32)
    (local $c i32)
    (local.set $c (global.get $c_card))
    (if (i32.eqz (local.get $fn))
      (then
        (if (i32.eq (local.get $a) (i32.const 3)) (then (return (i32.load8_u offset=0x02 (local.get $c)))))
        (if (i32.eq (local.get $a) (i32.const 5)) (then (return (i32.shl (global.get $c_level) (i32.const 1)))))
        (if (i32.lt_u (local.get $a) (i32.const 0x800)) (then (return (i32.load8_u (i32.add (local.get $c) (local.get $a))))))
        (return (i32.const 0))))
    (if (i32.ne (local.get $fn) (i32.const 1)) (then (return (i32.const 0))))
    (if (i32.eq (local.get $a) (i32.const 0x05)) (then (return (global.get $hint))))
    (if (i32.eq (local.get $a) (i32.const 0x10)) (then (return (i32.and (global.get $fw_req) (i32.const 255)))))
    (if (i32.eq (local.get $a) (i32.const 0x11)) (then (return (i32.shr_u (global.get $fw_req) (i32.const 8)))))
    (if (i32.eq (local.get $a) (i32.const 0x20)) (then (return (i32.const 0x0F))))
    (if (i32.eq (local.get $a) (i32.const 0x34)) (then (return (i32.and (global.get $scratch) (i32.const 255)))))
    (if (i32.eq (local.get $a) (i32.const 0x35)) (then (return (i32.shr_u (global.get $scratch) (i32.const 8)))))
    (if (i32.lt_u (local.get $a) (i32.const 0x100)) (then (return (i32.load8_u offset=0x800 (i32.add (local.get $c) (local.get $a))))))
    (i32.const 0))

  (func $card_write (param $fn i32) (param $a i32) (param $v i32)
    (local $c i32)
    (local.set $c (global.get $c_card))
    (if (i32.eqz (local.get $fn))
      (then
        (if (i32.ge_u (local.get $a) (i32.const 0x800)) (then (return)))
        (if (i32.le_u (local.get $a) (i32.const 1)) (then (return)))
        (if (i32.eq (local.get $a) (i32.const 3)) (then (return)))
        (if (i32.eq (local.get $a) (i32.const 5)) (then (return)))
        (if (i32.and (i32.ge_u (local.get $a) (i32.const 8)) (i32.le_u (local.get $a) (i32.const 0xB))) (then (return)))
        (if (i32.eq (local.get $a) (i32.const 0x100)) (then (return)))
        (i32.store8 (i32.add (local.get $c) (local.get $a)) (local.get $v))
        (if (i32.eq (local.get $a) (i32.const 6))      ;; ABORT/RES bit3 = card reset
          (then (if (i32.and (local.get $v) (i32.const 8)) (then (call $card_reset)))))
        (if (i32.eq (local.get $a) (i32.const 4)) (then (call $card_update_irq (i32.const 0))))
        (return)))
    (if (i32.ne (local.get $fn) (i32.const 1)) (then (return)))
    (if (i32.eq (local.get $a) (i32.const 0x05))
      (then
        (global.set $hint (i32.and (global.get $hint) (local.get $v)))
        (call $card_update_irq (i32.const 1))
        (return)))
    (if (i32.ge_u (local.get $a) (i32.const 0x100)) (then (return)))
    (i32.store8 offset=0x800 (i32.add (local.get $c) (local.get $a)) (local.get $v))
    (if (i32.eq (local.get $a) (i32.const 0x03)) (then (call $clog (i32.const 8) (local.get $v))))
    (if (i32.and (i32.eq (local.get $a) (i32.const 0x03)) (i32.ne (i32.and (local.get $v) (i32.const 2)) (i32.const 0)))
      (then (if (global.get $c_sleep)
        (then (global.set $c_sleep (i32.const 0)) (call $clog (i32.const 6) (i32.const 0))
              (global.set $hint (i32.or (global.get $hint) (i32.const 2)))
              (call $card_event (i32.const 0x10))))))
    (if (i32.eq (local.get $a) (i32.const 0x04)) (then (call $card_update_irq (i32.const 1)))))

  ;; event packet: type 3, payload u32 code
  (func $card_event (param $code i32)
    (local $t i32)
    (local.set $t (i32.add (global.get $c_card) (i32.const 0x2B800)))
    (i32.store (local.get $t) (local.get $code))
    (call $clog (i32.const 7) (local.get $code))
    (call $card_enqueue (i32.const 3) (local.get $t) (i32.const 4)))

  ;; ---- upload queue (card -> host packets) ----------------------------
  ;; enqueue {u16 total, u16 type, payload[n]}
  (func $card_enqueue (param $type i32) (param $src i32) (param $n i32)
    (local $s i32)
    (if (i32.gt_u (local.get $n) (i32.const 2044)) (then (local.set $n (i32.const 2044))))
    (if (i32.ge_u (i32.sub (global.get $q_head) (global.get $q_tail)) (i32.const 16))
      (then (call $clog (i32.const 0x7F) (local.get $type)) (return)))
    (local.set $s (i32.add (i32.add (global.get $c_card) (i32.const 0x23000))
                           (i32.shl (i32.and (global.get $q_head) (i32.const 15)) (i32.const 11))))
    (i32.store16 (local.get $s) (i32.add (local.get $n) (i32.const 4)))
    (i32.store16 offset=2 (local.get $s) (local.get $type))
    (memory.copy (i32.add (local.get $s) (i32.const 4)) (local.get $src) (local.get $n))
    (global.set $q_head (i32.add (global.get $q_head) (i32.const 1)))
    (call $card_next_upload))

  (func $card_next_upload
    (local $c i32) (local $s i32) (local $n i32) (local $u i32)
    (if (global.get $up_len) (then (return)))
    (if (i32.ne (global.get $fw_stage) (i32.const 2)) (then (return)))
    (local.set $c (global.get $c_card))
    (local.set $u (i32.add (local.get $c) (i32.const 0x1000)))
    (if (i32.ne (global.get $q_head) (global.get $q_tail))
      (then
        (local.set $s (i32.add (i32.add (local.get $c) (i32.const 0x23000))
                               (i32.shl (i32.and (global.get $q_tail) (i32.const 15)) (i32.const 11))))
        (local.set $n (i32.load16_u (local.get $s)))
        (memory.copy (local.get $u) (local.get $s) (local.get $n))
        (global.set $q_tail (i32.add (global.get $q_tail) (i32.const 1))))
      (else
        ;; data frame from the host RX ring, only once associated
        (if (i32.eqz (global.get $c_assoc)) (then (return)))
        (memory.fill (local.get $u) (i32.const 0) (i32.const 28))
        (local.set $n (call $emu_net_rx_pop (i32.add (local.get $u) (i32.const 28)) (i32.const 2044)))
        (if (i32.le_s (local.get $n) (i32.const 0)) (then (return)))
        (call $clog (i32.const 3) (local.get $n))
        ;; RxPD (24 B): status=1, snr, pkt_len @4, nf, rate, pkt offset @8 = 24
        (i32.store16 offset=4 (local.get $u) (i32.const 1))
        (i32.store8 offset=6 (local.get $u) (i32.const 40))
        (i32.store16 offset=8 (local.get $u) (local.get $n))
        (i32.store8 offset=10 (local.get $u) (i32.const 0x80))
        (i32.store offset=12 (local.get $u) (i32.const 24))
        (local.set $n (i32.add (local.get $n) (i32.const 28)))
        (i32.store16 (local.get $u) (local.get $n))
        (i32.store16 offset=2 (local.get $u) (i32.const 0))))
    (global.set $up_len (local.get $n))
    (global.set $scratch (local.get $n))
    (global.set $hint (i32.or (global.get $hint) (i32.const 1)))
    (call $card_update_irq (i32.const 1)))

  ;; ---- Marvell host commands -------------------------------------------
  ;; c = command (u16 code, u16 size, u16 seq, u16 result, body), n bytes
  (func $card_command (param $c i32) (param $n i32)
    (local $t i32) (local $code i32) (local $size i32)
    (local.set $t (i32.add (global.get $c_card) (i32.const 0x2B000)))
    (local.set $code (i32.load16_u (local.get $c)))
    (global.set $c_cmds (i32.add (global.get $c_cmds) (i32.const 1)))
    (call $clog (i32.const 1) (local.get $code))
    ;; keep a copy of the last command of each of a few kinds for debugging:
    ;; 0x2BC00 last SCAN (256 B), 0x2BD00 last other (256 B)
    (memory.copy (i32.add (global.get $c_card) (select (i32.const 0x2BC00) (i32.const 0x2BD00) (i32.eq (local.get $code) (i32.const 6))))
                 (local.get $c) (select (local.get $n) (i32.const 256) (i32.lt_u (local.get $n) (i32.const 256))))
    (if (i32.gt_u (local.get $n) (i32.const 2000)) (then (local.set $n (i32.const 2000))))
    (memory.fill (local.get $t) (i32.const 0) (i32.const 2048))
    (memory.copy (local.get $t) (local.get $c) (local.get $n))
    (local.set $size (i32.load16_u offset=2 (local.get $c)))
    (if (i32.gt_u (local.get $size) (i32.const 2000)) (then (local.set $size (i32.const 2000))))
    (if (i32.lt_u (local.get $size) (i32.const 8)) (then (local.set $size (i32.const 8))))
    ;; 0x3E DEEP_SLEEP: real firmware sends no response; the card sleeps until
    ;; the host sets FN1 config bit1 (HOST_POWER_UP), then reports event 0x10
    ;; (DEEP_SLEEP_AWAKE: handler c0387768 cancels the timer and sets fPSState=0)
    (if (i32.eq (local.get $code) (i32.const 0x3E))
      (then (global.set $c_sleep (i32.const 1)) (call $clog (i32.const 6) (i32.const 1)) (return)))
    ;; 0x06 SCAN: answer with the fake open AP "LocalNet" on channel 6 if the
    ;; channel-list TLV (0x0101) contains channel 6
    (if (i32.eq (local.get $code) (i32.const 0x06))
      (then
        (local.set $size (i32.const 11))
        (i32.store16 offset=8 (local.get $t) (i32.const 0))
        (i32.store8 offset=10 (local.get $t) (i32.const 0))
        (if (call $scan_has_chan (local.get $c) (local.get $n) (i32.const 6))
          (then
            (local.set $size (i32.add (i32.const 11) (call $bss_localnet (i32.add (local.get $t) (i32.const 11)))))
            (i32.store16 offset=8 (local.get $t) (i32.sub (local.get $size) (i32.const 11)))
            (i32.store8 offset=10 (local.get $t) (i32.const 1))
            (call $clog (i32.const 9) (i32.const 6))))))
    ;; 0x4D MAC_ADDRESS: remember the MAC (body: u16 action, mac[6])
    (if (i32.eq (local.get $code) (i32.const 0x4D))
      (then (memory.copy (i32.add (global.get $c_card) (i32.const 0x8F0)) (i32.add (local.get $c) (i32.const 10)) (i32.const 6))))
    ;; 0x03 GET_HW_SPEC
    (if (i32.eq (local.get $code) (i32.const 0x03))
      (then
        (i32.store16 offset=8 (local.get $t) (i32.const 2))        ;; hw if version
        (i32.store16 offset=10 (local.get $t) (i32.const 0x8686))  ;; hw version
        (i32.store16 offset=12 (local.get $t) (i32.const 1))       ;; nr txpd
        (i32.store16 offset=14 (local.get $t) (i32.const 32))      ;; nr mcast
        (memory.copy (i32.add (local.get $t) (i32.const 16)) (i32.add (global.get $c_card) (i32.const 0x8F0)) (i32.const 6))
        (i32.store16 offset=22 (local.get $t) (i32.const 0x10))    ;; region FCC
        (i32.store16 offset=24 (local.get $t) (i32.const 1))       ;; antennas
        (i32.store offset=26 (local.get $t) (i32.const 0x09460318)) ;; fw 9.70.3.p24
        (i32.store offset=42 (local.get $t) (i32.const 0x00000303))
        (if (i32.lt_u (local.get $size) (i32.const 46)) (then (local.set $size (i32.const 46))))))
    ;; 0x50 ASSOCIATE (and 0x12): the 8686 answers with code 0x8012 (the driver's
    ;; handler c03883e8 sits in the 0x8012 slot; 0x8050 is unhandled).
    ;; body: u16 capab, u16 status (0 = success), u16 aid, IEs
    (if (i32.or (i32.eq (local.get $code) (i32.const 0x50)) (i32.eq (local.get $code) (i32.const 0x12)))
      (then
        (local.set $code (i32.const 0x12))
        (memory.fill (i32.add (local.get $t) (i32.const 8)) (i32.const 0) (i32.const 64))
        (i32.store16 offset=8 (local.get $t) (i32.const 0x0001))
        (i32.store16 offset=10 (local.get $t) (i32.const 0))
        (i32.store16 offset=12 (local.get $t) (i32.const 0xC001))
        (i32.store16 offset=14 (local.get $t) (i32.const 0x0801))
        (i64.store offset=16 (local.get $t) (i64.const 0x2418120C968B8482))
        (local.set $size (i32.const 24))
        (global.set $c_assoc (i32.const 1))
        (call $clog (i32.const 10) (i32.const 1))))
    (i32.store16 (local.get $t) (i32.or (local.get $code) (i32.const 0x8000)))
    (i32.store16 offset=2 (local.get $t) (local.get $size))
    (i32.store16 offset=6 (local.get $t) (i32.const 0))
    (call $card_enqueue (i32.const 1) (local.get $t) (local.get $size)))

  ;; scan command body: +8 bsstype, +9 bssid[6], TLVs from +15 {u16 type, u16 len}
  (func $scan_has_chan (param $c i32) (param $n i32) (param $ch i32) (result i32)
    (local $o i32) (local $ty i32) (local $l i32) (local $i i32)
    (local.set $o (i32.const 15))
    (block $done
      (loop $tlv
        (br_if $done (i32.gt_u (i32.add (local.get $o) (i32.const 4)) (local.get $n)))
        (local.set $ty (i32.load16_u (i32.add (local.get $c) (local.get $o))))
        (local.set $l (i32.load16_u offset=2 (i32.add (local.get $c) (local.get $o))))
        (if (i32.eq (local.get $ty) (i32.const 0x0101))
          (then
            (local.set $i (i32.const 0))
            (loop $ent
              (if (i32.lt_u (i32.add (local.get $i) (i32.const 2)) (local.get $l))
                (then
                  (if (i32.eq (i32.load8_u offset=5 (i32.add (i32.add (local.get $c) (local.get $o)) (local.get $i))) (local.get $ch))
                    (then (return (i32.const 1))))
                  (local.set $i (i32.add (local.get $i) (i32.const 7)))
                  (br $ent))))))
        (local.set $o (i32.add (i32.add (local.get $o) (i32.const 4)) (local.get $l)))
        (br $tlv)))
    (i32.const 0))

  ;; writes one scan-response BSS record at $p, returns its total length
  ;; {u16 len, bssid[6], rssi, tsf[8], beacon int, capab, IEs}
  (func $bss_localnet (param $p i32) (result i32)
    (memory.fill (local.get $p) (i32.const 0) (i32.const 64))
    ;; bssid 02:4c:4f:43:41:4c ("\x02LOCAL")
    (i32.store offset=2 (local.get $p) (i32.const 0x434F4C02))
    (i32.store16 offset=6 (local.get $p) (i32.const 0x4C41))
    (i32.store8 offset=8 (local.get $p) (i32.const 40))          ;; rssi -40 dBm
    (i32.store16 offset=17 (local.get $p) (i32.const 100))       ;; beacon interval
    (i32.store16 offset=19 (local.get $p) (i32.const 0x0001))    ;; capab: ESS, open
    ;; SSID IE "LocalNet"
    (i32.store16 offset=21 (local.get $p) (i32.const 0x0800))
    (i64.store offset=23 (local.get $p) (i64.const 0x74654E6C61636F4C))
    ;; supported rates 1 2 5.5 11 (basic) 6 9 12 18
    (i32.store16 offset=31 (local.get $p) (i32.const 0x0801))
    (i64.store offset=33 (local.get $p) (i64.const 0x2418120C968B8482))
    ;; DS parameter set: channel 6
    (i32.store offset=41 (local.get $p) (i32.const 0x00060103))
    ;; extended rates 24 36 48 54
    (i32.store16 offset=44 (local.get $p) (i32.const 0x0432))
    (i32.store offset=46 (local.get $p) (i32.const 0x6C604830))
    (i32.store16 (local.get $p) (i32.const 48))                 ;; len = 50 - 2
    (i32.const 50))

  ;; ---- CMD53 data phase -------------------------------------------------
  (func $card_download (param $fn i32) (param $p i32) (param $len i32)
    (local $l i32) (local $c i32) (local $rem i32) (local $off i32)
    (local.set $c (global.get $c_card))
    (if (i32.ne (local.get $fn) (i32.const 1)) (then (return)))
    (if (i32.eqz (global.get $fw_stage))
      (then
        (local.set $l (i32.load (local.get $p)))
        (if (i32.eqz (local.get $l))
          (then
            (global.set $fw_stage (i32.const 1))
            (global.set $fw_req (i32.const 256))
            (call $clog (i32.const 4) (global.get $fw_hlen))
            (return)))
        (if (i32.gt_u (local.get $l) (i32.sub (local.get $len) (i32.const 4)))
          (then (local.set $l (i32.sub (local.get $len) (i32.const 4)))))
        (if (i32.le_u (i32.add (global.get $fw_hlen) (local.get $l)) (i32.const 4096))
          (then (memory.copy (i32.add (i32.add (local.get $c) (i32.const 0x2000)) (global.get $fw_hlen))
                             (i32.add (local.get $p) (i32.const 4)) (local.get $l))))
        (global.set $fw_hlen (i32.add (global.get $fw_hlen) (local.get $l)))
        (return)))
    (if (i32.eq (global.get $fw_stage) (i32.const 1))
      (then
        (local.set $l (global.get $fw_req))
        (if (i32.gt_u (local.get $l) (local.get $len)) (then (local.set $l (local.get $len))))
        (if (i32.le_u (i32.add (global.get $fw_len) (local.get $l)) (i32.const 0x20000))
          (then (memory.copy (i32.add (i32.add (local.get $c) (i32.const 0x3000)) (global.get $fw_len))
                             (local.get $p) (local.get $l))))
        (global.set $fw_len (i32.add (global.get $fw_len) (local.get $l)))
        (local.set $rem (i32.sub (i32.const 122608) (global.get $fw_len)))
        (if (i32.le_s (local.get $rem) (i32.const 0))
          (then
            (global.set $fw_req (i32.const 0))
            (global.set $fw_stage (i32.const 2))
            (global.set $scratch (i32.const 0xFEDC))
            (call $clog (i32.const 5) (global.get $fw_len)))
          (else
            (global.set $fw_req (select (i32.const 256) (local.get $rem) (i32.gt_u (local.get $rem) (i32.const 256))))))
        (return)))
    ;; running: {u16 len, u16 type, payload}
    (local.set $l (i32.load16_u (local.get $p)))
    (if (i32.gt_u (local.get $l) (local.get $len)) (then (local.set $l (local.get $len))))
    (if (i32.lt_u (local.get $l) (i32.const 4)) (then (call $clog (i32.const 0x7E) (local.get $l)) (return)))
    (if (i32.eq (i32.load16_u offset=2 (local.get $p)) (i32.const 1))
      (then (call $card_command (i32.add (local.get $p) (i32.const 4)) (i32.sub (local.get $l) (i32.const 4)))))
    (if (i32.eqz (i32.load16_u offset=2 (local.get $p)))
      (then
        ;; TxPD: u32 pkt offset @8, u16 pkt len @12 (relative to the TxPD)
        (local.set $off (i32.load offset=12 (local.get $p)))
        (local.set $rem (i32.load16_u offset=16 (local.get $p)))
        (call $clog (i32.const 2) (local.get $rem))
        (if (i32.le_u (i32.add (i32.add (local.get $off) (local.get $rem)) (i32.const 4)) (local.get $l))
          (then (drop (call $emu_net_tx_push (i32.add (i32.add (local.get $p) (i32.const 4)) (local.get $off)) (local.get $rem)))))))
    ;; DNLD ready (H_INT bit1) after every download, except when the card just
    ;; went to deep sleep (a sleeping card must stay silent, otherwise the
    ;; driver's idle path sends HOST_SLEEP_CFG and wakes it again in a loop)
    (if (global.get $c_sleep) (then (return)))
    (global.set $hint (i32.or (global.get $hint) (i32.const 2)))
    (call $card_update_irq (i32.const 1)))

  (func $card_upload (param $fn i32) (param $p i32) (param $len i32)
    (local $n i32)
    (memory.fill (local.get $p) (i32.const 0) (local.get $len))
    (if (i32.or (i32.ne (local.get $fn) (i32.const 1)) (i32.eqz (global.get $up_len))) (then (return)))
    (local.set $n (global.get $up_len))
    (if (i32.gt_u (local.get $n) (local.get $len)) (then (local.set $n (local.get $len))))
    (memory.copy (local.get $p) (i32.add (global.get $c_card) (i32.const 0x1000)) (local.get $n))
    (global.set $up_len (i32.const 0))
    (global.set $scratch (i32.const 0))
    (call $card_next_upload))

  ;; DCTRL=0x10: run the pending CMD53 DMA, then raise IRQ bit0
  (func $host_dma
    (local $len i32) (local $p i32)
    (if (i32.eqz (global.get $x_pend)) (then (return)))
    (global.set $x_pend (i32.const 0))
    (global.set $c_dmas (i32.add (global.get $c_dmas) (i32.const 1)))
    (local.set $len (i32.mul (call $regs_load (i32.const 0x48) (i32.const 4)) (call $regs_load (i32.const 0x4C) (i32.const 4))))
    (if (i32.gt_u (local.get $len) (i32.const 0x10000)) (then (local.set $len (i32.const 0x10000))))
    (local.set $p (call $gptr (call $regs_load (i32.const 0x44) (i32.const 4)) (local.get $len)))
    (if (local.get $p)
      (then
        (if (global.get $x_write)
          (then (call $card_download (global.get $x_fn) (local.get $p) (local.get $len)))
          (else (call $card_upload (global.get $x_fn) (local.get $p) (local.get $len)))))
      (else (call $clog (i32.const 0x7D) (local.get $len))))
    (global.set $m_irq (i32.or (global.get $m_irq) (i32.const 1)))
    (call $host_update_line))

  ;; execute a command (CMD written with bit31)
  (func $host_cmd (param $idx i32) (param $arg i32)
    (local $r i32) (local $fn i32) (local $a i32)
    (local.set $r (i32.const 0))
    (if (i32.eq (local.get $idx) (i32.const 5))
      (then
        (if (i32.eqz (local.get $arg)) (then (call $card_reset)))
        (local.set $r (i32.const 0x90FF8000))))
    (if (i32.eq (local.get $idx) (i32.const 3)) (then (local.set $r (i32.const 0x00010000))))
    (if (i32.eq (local.get $idx) (i32.const 7)) (then (local.set $r (i32.const 0x00000900))))
    (if (i32.eq (local.get $idx) (i32.const 52))
      (then
        (local.set $fn (i32.and (i32.shr_u (local.get $arg) (i32.const 28)) (i32.const 7)))
        (local.set $a (i32.and (i32.shr_u (local.get $arg) (i32.const 9)) (i32.const 0x1FFFF)))
        (if (i32.shr_u (local.get $arg) (i32.const 31))
          (then (call $card_write (local.get $fn) (local.get $a) (i32.and (local.get $arg) (i32.const 255)))))
        (local.set $r (i32.or (i32.const 0x1000) (call $card_read (local.get $fn) (local.get $a))))))
    (if (i32.eq (local.get $idx) (i32.const 53))
      (then
        (global.set $x_pend (i32.const 1))
        (global.set $x_write (i32.shr_u (local.get $arg) (i32.const 31)))
        (global.set $x_fn (i32.and (i32.shr_u (local.get $arg) (i32.const 28)) (i32.const 7)))
        (global.set $x_addr (i32.and (i32.shr_u (local.get $arg) (i32.const 9)) (i32.const 0x1FFFF)))
        (local.set $r (i32.const 0x1000))))
    (call $regs_store (i32.const 0x20) (i32.const 4) (local.get $r))
    (call $regs_store (i32.const 0x24) (i32.const 4) (i32.const 0))
    (call $regs_store (i32.const 0x28) (i32.const 4) (i32.const 0))
    (call $regs_store (i32.const 0x2C) (i32.const 4) (i32.const 0))
    (global.set $m_dsta (i32.or (global.get $m_dsta) (i32.const 0x10))))

  ;; mode-2 hooks (called from $sdio_read_hook / $sdio_write_hook)
  (func $model_read (param $off i32) (param $size i32) (param $val i32) (result i32)
    (if (i32.eqz (call $card_init)) (then (return (local.get $val))))
    (if (i32.eq (local.get $off) (i32.const 0x18)) (then (return (i32.or (global.get $m_dsta) (i32.const 1)))))
    (if (i32.eq (local.get $off) (i32.const 0x38)) (then (return (global.get $m_irq))))
    (call $regs_load (local.get $off) (local.get $size)))

  (func $model_write (param $off i32) (param $size i32) (param $val i32)
    (if (i32.eqz (call $card_init)) (then (return)))
    (if (i32.eq (local.get $off) (i32.const 0x08))
      (then (if (i32.shr_u (local.get $val) (i32.const 31))
              (then (call $host_cmd (i32.and (local.get $val) (i32.const 0x3F)) (call $regs_load (i32.const 0x0C) (i32.const 4)))))
            (return)))
    (if (i32.eq (local.get $off) (i32.const 0x14))
      (then (global.set $m_dsta (i32.and (global.get $m_dsta) (i32.xor (local.get $val) (i32.const -1)))) (return)))
    (if (i32.eq (local.get $off) (i32.const 0x38))
      (then (global.set $m_irq (i32.and (global.get $m_irq) (i32.xor (local.get $val) (i32.const -1))))
            (call $host_update_line) (return)))
    (if (i32.eq (local.get $off) (i32.const 0x3C))
      (then (global.set $m_irqmask (local.get $val)) (call $host_update_line) (return)))
    (if (i32.eq (local.get $off) (i32.const 0x04))
      (then (if (i32.and (local.get $val) (i32.const 0x10)) (then (call $host_dma))))))

  ;; emu_net_card(idx): 0 card ptr, 1 fw stage, 2 helper bytes, 3 fw bytes,
  ;; 4 helper capture ptr, 5 fw capture ptr, 6 log head, 7 log ptr (256 x u32),
  ;; 8 irq raises, 9 host IRQ, 10 IRQMASK, 11 fn1 int status, 12 upload len,
  ;; 13 card resets, 14 host cmds, 15 DMAs, 16 line, 17 associated
  (func $emu_net_card (param $i i32) (result i32)
    (if (i32.eqz (call $card_init)) (then (return (i32.const -1))))
    (if (i32.eqz (local.get $i)) (then (return (global.get $c_card))))
    (if (i32.eq (local.get $i) (i32.const 1)) (then (return (global.get $fw_stage))))
    (if (i32.eq (local.get $i) (i32.const 2)) (then (return (global.get $fw_hlen))))
    (if (i32.eq (local.get $i) (i32.const 3)) (then (return (global.get $fw_len))))
    (if (i32.eq (local.get $i) (i32.const 4)) (then (return (i32.add (global.get $c_card) (i32.const 0x2000)))))
    (if (i32.eq (local.get $i) (i32.const 5)) (then (return (i32.add (global.get $c_card) (i32.const 0x3000)))))
    (if (i32.eq (local.get $i) (i32.const 6)) (then (return (global.get $c_logh))))
    (if (i32.eq (local.get $i) (i32.const 7)) (then (return (i32.add (global.get $c_card) (i32.const 0x900)))))
    (if (i32.eq (local.get $i) (i32.const 8)) (then (return (global.get $m_raises))))
    (if (i32.eq (local.get $i) (i32.const 9)) (then (return (global.get $m_irq))))
    (if (i32.eq (local.get $i) (i32.const 10)) (then (return (global.get $m_irqmask))))
    (if (i32.eq (local.get $i) (i32.const 11)) (then (return (global.get $hint))))
    (if (i32.eq (local.get $i) (i32.const 12)) (then (return (global.get $up_len))))
    (if (i32.eq (local.get $i) (i32.const 13)) (then (return (global.get $c_resets))))
    (if (i32.eq (local.get $i) (i32.const 14)) (then (return (global.get $c_cmds))))
    (if (i32.eq (local.get $i) (i32.const 15)) (then (return (global.get $c_dmas))))
    (if (i32.eq (local.get $i) (i32.const 16)) (then (return (global.get $m_line))))
    (if (i32.eq (local.get $i) (i32.const 17)) (then (return (global.get $c_assoc))))
    (if (i32.eq (local.get $i) (i32.const 18)) (then (return (i32.add (global.get $c_card) (i32.const 0x2BC00)))))
    (i32.const -1))

  ;; host-side: try to start an upload (e.g. after emu_net_rx_push)
  (func $emu_net_card_kick (result i32)
    (if (i32.eqz (call $card_init)) (then (return (i32.const 0))))
    (call $card_next_upload)
    (global.get $up_len))
