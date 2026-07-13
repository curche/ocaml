(* A small program exercising every kind of shared-heap allocation
   ("ocaml dump-heap" should see them all): small pool blocks, a large
   block, and a string. *)

let allocate_stuff () =
  let small = Array.init 50 (fun i -> (i, i * i)) in
  let big = Array.make 100_000 0 in
  let str = String.make 5000 'x' in
  ignore (Sys.opaque_identity small);
  ignore (Sys.opaque_identity big);
  ignore (Sys.opaque_identity str);
  (small, big, str)

let () =
  let keep = allocate_stuff () in
  (* A blocking call, so the runtime's backup thread is likely already
     active by the time we stop below -- this exercises the
     domain/thread dedup path even with a single domain. *)
  print_endline "allocated";
  ignore (Sys.opaque_identity keep);
  print_endline "parked"
