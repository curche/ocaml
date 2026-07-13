(* TEST
   native-compiler;
   no-tsan; (* Skip, TSan may perturb allocation/thread counts *)
   linux;
   not-clang; (* Skip, clang is tested on macOS *)
   arch_amd64;
   script = "sh ${test_source_directory}/has_gdb.sh";
   script;
   readonly_files = "gdb_heapdump.ml";
   setup-ocamlopt.byte-build-env;
   program = "${test_build_directory}/gdb_heapdump";
   flags = "-g";
   all_modules = "gdb_heapdump.ml";
   ocamlopt.byte;
   debugger_script = "${test_source_directory}/gdb-heapdump-script";
   gdb;
   script = "sh ${test_source_directory}/sanitize.sh linux-gdb-heapdump-amd64";
   script;
   check-program-output;
 *)
