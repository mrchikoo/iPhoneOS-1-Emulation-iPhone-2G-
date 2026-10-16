  ;; ======================================================================
  ;; re-irq additions (inserted by irq_patch.py).  Appended after the last
  ;; original function (before the table), so no original function index
  ;; changes.  Works on the original emu.wat or on patch/out/emu-net.wat.
  ;;
  ;; Interrupt model of the original core (see IRQ_NOTES.md):
  ;;   machine struct at 23560 (static; func 233 passes i32.const 23560).
  ;;   VIC0 at machine+84200, VIC1 at machine+84216, 16 B each:
  ;;     +0 RAW (device line levels, written only by func 20)
  ;;     +4 INTENABLE  +8 INTSELECT (1=FIQ)  +12 SOFTINT
  ;;   func 20(vic, bit, level) sets/clears one RAW bit.
  ;;   func 19 (device tick) re-drives every device line through func 20,
  ;;   then recomputes the CPU lines:
  ;;     machine+272 (abs 23832) IRQ = any(EN & (RAW|SOFT) & ~SEL)
  ;;     machine+273 (abs 23833) FIQ = any(EN & (RAW|SOFT) &  SEL)
  ;;   func 80 (CPU step) takes the IRQ exception when byte 23832 is 1 and
  ;;   CPSR.I is clear.  The kernel then reads VIC0 VECTADDR (0x38E00F00),
  ;;   which func 97 answers with 0x80000000|line (42 for VIC1 bit 10).
  ;;
  ;; Line 42 = VIC1 RAW bit 10.  Nothing in the original core drives it.
  ;; The device level lives in $sdio_irq_level (inserted after global 0);
  ;; irq_patch.py also adds one call in func 19 that re-drives RAW bit 10
  ;; from it every tick, exactly like the built-in devices, so the bit can
  ;; never go stale (e.g. after a snapshot restore the level is 0 until the
  ;; device model raises it again).
  ;;
  ;; $sdio_irq_level is NOT saved in snapshots (RAW is, but func 19 re-drives
  ;; it from the global on the next tick).
  ;;
  ;; The global below must end up AFTER original global 0 in the text (so
  ;; `global.get 0` keeps meaning the stack pointer).  irq_patch.py (and
  ;; patch/patch.py) move every line matching r"^  \(global \$" there.
  ;; The func 19 hook is optional for correctness (nothing else in the core
  ;; writes VIC1 RAW bit 10, so the bit set by $sdio_set_irq persists); it
  ;; only keeps RAW consistent with the level after a snapshot restore.
  ;; Use irq_patch.apply_func19_hook(wat) to add it from another pipeline.
  ;; ======================================================================
  (global $sdio_irq_level (mut i32) (i32.const 0))

  ;; Recompute the CPU IRQ/FIQ lines from current VIC state (same formula as
  ;; the tail of func 19).  Pure function of VIC state -> safe to call at any
  ;; time, including from inside an MMIO hook; func 19 will rewrite the same
  ;; values on its next tick.
  (func $vic_irq_reeval
    (local $p0 i32) (local $s0 i32) (local $p1 i32) (local $s1 i32)
    ;; VIC0: pending = EN & (RAW | SOFT)
    i32.const 0
    i32.load offset=107764        ;; 23560+84204 VIC0 EN
    i32.const 0
    i32.load offset=107772        ;; VIC0 SOFT
    i32.const 0
    i32.load offset=107760        ;; VIC0 RAW
    i32.or
    i32.and
    local.set $p0
    i32.const 0
    i32.load offset=107768        ;; VIC0 SEL
    local.set $s0
    ;; VIC1
    i32.const 0
    i32.load offset=107780        ;; 23560+84220 VIC1 EN
    i32.const 0
    i32.load offset=107788        ;; VIC1 SOFT
    i32.const 0
    i32.load offset=107776        ;; VIC1 RAW
    i32.or
    i32.and
    local.set $p1
    i32.const 0
    i32.load offset=107784        ;; VIC1 SEL
    local.set $s1
    ;; IRQ line: machine+272
    i32.const 0
    local.get $p1
    local.get $s1
    i32.const -1
    i32.xor
    i32.and
    i32.const 0
    i32.ne
    local.get $p0
    local.get $s0
    i32.const -1
    i32.xor
    i32.and
    i32.const 0
    i32.ne
    i32.or
    i32.store8 offset=23832
    ;; FIQ line: machine+273
    i32.const 0
    local.get $p1
    local.get $s1
    i32.and
    i32.const 0
    i32.ne
    local.get $p0
    local.get $s0
    i32.and
    i32.const 0
    i32.ne
    i32.or
    i32.store8 offset=23833)

  ;; Set the SDIO interrupt level (line 42 = VIC1 bit 10).  Level-triggered:
  ;; the line stays asserted until $sdio_lower_irq.  Effective immediately
  ;; (RAW + CPU line), and re-driven by func 19 every tick.
  (func $sdio_set_irq (param $level i32)
    local.get $level
    i32.const 0
    i32.ne
    global.set $sdio_irq_level
    i32.const 107776              ;; VIC1 RAW (machine+84216)
    i32.const 10
    global.get $sdio_irq_level
    call 20
    call $vic_irq_reeval)

  (func $sdio_raise_irq
    i32.const 1
    call $sdio_set_irq)

  (func $sdio_lower_irq
    i32.const 0
    call $sdio_set_irq)

  ;; sdio_irq_state(idx): 0 level, 1 VIC1 RAW, 2 VIC1 EN, 3 VIC1 SEL,
  ;; 4 VIC1 SOFT, 5 CPU IRQ line byte, 6 CPU FIQ line byte,
  ;; 7 VIC0 RAW, 8 VIC0 EN, 9 version (0x49525131 'IRQ1')
  (func $sdio_irq_state (param $i i32) (result i32)
    block
      block
        block
          block
            block
              block
                block
                  block
                    block
                      block
                        local.get $i
                        br_table 0 1 2 3 4 5 6 7 8 9 9
                      end
                      global.get $sdio_irq_level
                      return
                    end
                    i32.const 0
                    i32.load offset=107776
                    return
                  end
                  i32.const 0
                  i32.load offset=107780
                  return
                end
                i32.const 0
                i32.load offset=107784
                return
              end
              i32.const 0
              i32.load offset=107788
              return
            end
            i32.const 0
            i32.load8_u offset=23832
            return
          end
          i32.const 0
          i32.load8_u offset=23833
          return
        end
        i32.const 0
        i32.load offset=107760
        return
      end
      i32.const 0
      i32.load offset=107764
      return
    end
    i32.const 0x49525131)
