#**************************************************************************
#*                                                                        *
#*                                 OCaml                                  *
#*                                                                        *
#*                           Nick Barnes, Tarides                         *
#*                                                                        *
#*   Copyright 2024 Tarides.                                              *
#*                                                                        *
#*   All rights reserved.  This file is distributed under the terms of    *
#*   the GNU Lesser General Public License version 2.1, with the          *
#*   special exception on linking described in the file LICENSE.          *
#*                                                                        *
#**************************************************************************

# This file contains any debugger-agnostic code for debugger plugins.
#
# Each debugger front end has three ciasses - targets, types, and
# values, which must provide these slots and methods:
#
# targets:
#
#     word_size: size of a word in 8-bit bytes.
#
#     double_size: the number of 8-bit bytes in a double-precision
#        float (i.e. 8).
#
#     value(address): a debugger "value" representing the given address
#         with type `value`.
#
#     global_variable(name): the value of a named global variable.
#
#     type(name): a named type.
#
#     symbol(address): the symbol name associated with the given address.
#
#     mapping(address): a string describing any file mapping
#         associated with the address, or None. Blocks on the Caml
#         heap do not have an associated file mapping.
#
# types:
#
#     size(): the number of bytes required for this type.
#
#     pointer(): the type of a pointer to this type.
#
#     array(size): the type of an array of given size of this type.
#
# values:
#
#     valid(): False if this value is somehow "invalid" (for example,
#       optimised away.
#
#     type(): The type of this value.
#
#     unsigned(): the value as an unsigned integer.
#
#     signed(): the value as a signed integer.
#
#     cast(t): the value cast to type `t`.
#
#     value(): the value cast to the "value" Caml runtime type.
#
#     pointer(): the value cast to "value*"
#
#     dereference(): the result of dereferencing the value, which must
#       be a pointer.
#
#     array_size(): only used when the value is an array, returns the
#       number of entries in the array.
#
#     sub(index): only used when the value is an array, returns an
#       entry, as a debugger value. TODO: switch to __getitem__ to make
#       this more transparent.
#
#     struct(): a dictionary {slot_name: value} representing the value,
#       which must be a struct. TODO: could use __getattribute__ to make
#       this more transparent.
#
#     field(index): the `index`th field of a value array whose address is
#       in the value, which can have any scalar type. `index` is a Python
#       number which can have any value, including negative.
#
#     field_pointer(index): a pointer to the `index`th field (see above).
#
#     byte_field(index): a byte value, from a byte array.
#
#     double_field(index): a double-precision floating-point value
#       from an array, as a Python float.
#
#     string(length): treating the value as an address, `length` bytes from
#       memory, decoded as UTF-8, as a Python string.
#
#     c_string(): treating the value as an address, returns the NUL-terminated
#       C string at the location as a Python string.
#
#     field_array(offset, size): an array of elements [offset,
#       offset+size), as a debugger-native array.
#
#     double_array(size): an array of double-precision floating point
#       members, as a debugger-native array.
#
#     pointer_index(index): treating the value as a pointer p, returning
#       p[index] as a value. TODO: switch to __getitem__.

import json
import os

MAX_BLOCK_SLOTS = 8
MAX_STRING_LEN = 80
STRING_SUFFIX = 8
STRING_PREFIX = MAX_STRING_LEN - STRING_SUFFIX - 5
MAX_STRING_SUMMARY = 20
STRING_SUMMARY_PREFIX = 8
STRING_SUMMARY_SUFFIX = MAX_STRING_SUMMARY - STRING_SUMMARY_PREFIX - 5

TAGS = {
    244: 'Forcing',
    245: 'Cont',
    246: 'Lazy',
    247: 'Closure',
    248: 'Object',
    249: 'Infix',
    250: 'Forward',
    251: 'Abstract',
    252: 'String',
    253: 'Double',
    254: 'Double_array',
    255: 'Custom'
}

# specific tag values which we display in particular ways.

TAG_CLOSURE = 247
TAG_INFIX = 249
TAG_STRING = 252
TAG_DOUBLE = 253
TAG_DOUBLE_ARRAY = 254
TAG_CUSTOM = 255

# constants for header word decoding.

HEADER_TAG_BITS = 8
HEADER_TAG_MASK = (1 << HEADER_TAG_BITS) - 1

HEADER_COLOR_BITS = 2
HEADER_COLOR_SHIFT = HEADER_TAG_BITS
HEADER_COLOR_MASK = ((1 << HEADER_COLOR_BITS) - 1) << HEADER_COLOR_SHIFT
NOT_MARKABLE = 3 << HEADER_COLOR_SHIFT

HEADER_WOSIZE_SHIFT = HEADER_TAG_BITS + HEADER_COLOR_BITS

# tags of this or above indicate blocks with no scannable fields
NO_SCAN_TAG = 251

# The debug runtime fills free and uninitialized memory with words:
#
#             D7xx D6D8 on 32-bit platforms
#   D7xx D7D7 D7xx D6D8 in 64-bit platforms
#
# where xx is one of the following 8-bit values, depending on the
# context of the memory word.

DEBUG_TAGS = {
    0x00: 'free minor',
    0x01: 'free major',
    0x03: 'free shrink',
    0x04: 'free truncate', # obsolete
    0x05: 'free unused',
    0x10: 'uninit minor',
    0x11: 'uninit major',
    0x15: 'uninit align',
    0x85: 'filler align',
    0x99: 'pool magic',
}

DEBUG_LOW_BYTES = [0xd8, 0xd6]
DEBUG_OTHER = 0xd7
DEBUG_TAG_BYTES = [2, 6]

def debug_decode(word, word_size):
    """If `word` is a debug padding word, return a string representation of
it. Otherwise, return None. `target` is used for word size."""

    if (word >> (word_size * 8)) not in {0,-1}:
        return
    bytes = [(word >> (i * 8)) & 0xff for i in range(word_size)]
    if bytes[:len(DEBUG_LOW_BYTES)] != DEBUG_LOW_BYTES:
        return
    pads = set(bytes[i]
               for i in range(len(DEBUG_LOW_BYTES), word_size)
               if i not in DEBUG_TAG_BYTES)
    if pads != {DEBUG_OTHER}: # not all pad bytes DEBUG_OTHER
        return
    tags = set(bytes[i] for i in DEBUG_TAG_BYTES if i < word_size)
    if len(tags) != 1: # differing tags on 64-bits
        return
    tag = list(tags)[0] # unique tag byte
    if tag not in DEBUG_TAGS:
        return f'Debug(0x{tag:x}?!)'
    return f'Debug({DEBUG_TAGS[tag]})'

# we show colors as [x], for some character x:

COLOR_SUMMARY = {
    'MARKED': 'm',
    'UNMARKED': 'u',
    'GARBAGE': 'g',
    'NOT MARKABLE': '-',
}

def colors(target):
    """Return a dictionary value -> name of the current GC colors
    (MARKED, UNMARKED, GARBAGE, NOT MARKABLE).
    """

    heapState = target.global_variable('caml_global_heap_state').struct()
    cols = {v.unsigned(): (f, COLOR_SUMMARY[f])
            for f, v in heapState.items()}
    cols[NOT_MARKABLE] = ('NOT MARKABLE', '-')
    return cols

class Value:
    def __init__(self, value, target):
        self._value = value
        self._target = target
        self.children = False
        self.num_children = 0
        self.valid = value.valid()
        if not self.valid:
            return

        self.word = value.signed()
        if self.word == 0:
            self.valid = False
            return

        self.immediate = (self.word & 1) == 1
        if self.immediate:
            return

        self.debug = debug_decode(self.word, target.word_size)
        if self.debug is not None:
            return

        self.pointer = value.pointer()
        self._header = self.pointer.field(-1).unsigned()
        self._wosize = self._header >> HEADER_WOSIZE_SHIFT
        self._tag = self._header & HEADER_TAG_MASK
        self._color_bits = self._header & HEADER_COLOR_MASK
        self.children = True
        self.num_children = self._wosize # overridden for some tags
        if self._tag == TAG_DOUBLE:
            self.children = False
            self.num_children = 0
        elif self._tag == TAG_DOUBLE_ARRAY:
            self.num_children = ((self.num_children * target.word_size)
                                 // target.double_size)
        elif self._tag == TAG_STRING:
            self.children = False
            self.num_children = 0
            byteSize = target.word_size * self._wosize
            lastByte = value.byte_field(byteSize-1).unsigned()
            self._length = byteSize-1-lastByte
            if self._length > 0:
                self._string = value.string(self._length)
            else:
                self._string = ''
        elif self._tag == TAG_CLOSURE:
            # collect code pointers and metadata for all functions
            # in this closure.
            self._functions = []
            # list of (code, arity [, additional code]) tuples.
            self._infix_map = {}
            # map from infix offset to tuple
            arity_shift = target.word_size * 8 - 8
            closinfo = value.field(1).signed()
            self._start_env = (closinfo & ((1 << arity_shift) - 1)) >> 1
            self.num_children = self._wosize - self._start_env
            block = 0
            while block < self._start_env:
                code = value.field(block).unsigned()
                closinfo = value.field(block+1).unsigned()
                arity = closinfo >> arity_shift
                if (arity == 0) or (arity == 1):
                    fn = (code, arity)
                    bump = 0
                else: # higher arity, so code is curry/tuplify
                    true_code = value.field(block+2).unsigned()
                    fn = (true_code, arity, code)
                    bump = 1
                self._functions.append(fn)
                self._infix_map[block] = fn
                block += 3 +bump # code, closinfo, [extra code], [infix header]
        elif self._tag == TAG_INFIX:
            self._container = Value(value.field_pointer(-self._wosize), target)
            self.num_children = 0
            self.children = False
        elif self._tag == TAG_CUSTOM:
            ptr_type = target.type('struct custom_operations').pointer()
            self._ops = value.field(0).cast(ptr_type).dereference().struct()
            self._id = self._ops['identifier'].c_string()
            self.children = False
            self.num_children = 0

    def tag_part(self):
        if self._tag in TAGS:
            return f'{TAGS[self._tag]}'
        elif self._tag == 0:
            return ''
        else:
            return f't{self._tag}'

    def infix_sym(self):
        cont = self._container
        sym = f'+{self._wosize}'
        # try to find symbol in infix map of container
        if cont._tag == TAG_CLOSURE: # always true!
            if self._wosize in cont._infix_map:
                code = cont._infix_map[self._wosize][0]
                sym = self._target.symbol(code)
                if sym is None:
                    sym = f'0x{code:x}'
        return sym

    def code_sym(self, t):
        code = t[0]
        sym = self._target.symbol(code)
        if sym is None:
            sym = f'0x{code:x}'
        if len(t) == 2:
            return sym
        else:
            return f'{sym}({self._target.symbol(t[2])})'

    def closure_syms(self):
        return [self.code_sym(t) for t in self._functions]

    def array_contents(self, short=False):
        if self._tag == TAG_DOUBLE_ARRAY:
            if self.num_children <= MAX_BLOCK_SLOTS:
                return [str(self._value.double_field(i))
                        for i in range(self.num_children)]
            return ([str(self._value.double_field(i))
                     for i in range(MAX_BLOCK_SLOTS-2)]
                    + ['...',
                       str(self._value.double_field(
                           self.num_children - 1))])

        if self.num_children < MAX_BLOCK_SLOTS:
            return [self.field_summary(i, short)
                    for i in range(self.num_children)]
        else:
            return ([self.field_summary(i, short)
                     for i in range(MAX_BLOCK_SLOTS-2)]
                    + ['...',
                       self.field_summary(self.num_children - 1, short)])

    def summary(self, short=False):
        """Return a short value summary string, suitable for display in a
        larger aggregate. If `short` then summarise the summary.
        """
        if not self.valid:
            return '[invalid]'
        if self.immediate:
            return f'{self.word // 2}'
        if self.debug is not None:
            return self.debug

        if self._tag == TAG_DOUBLE:
            return str(self._value.double_field(0))
        elif self._tag == TAG_STRING:
            if self._length > MAX_STRING_SUMMARY:
                return (repr(self._string[:STRING_SUMMARY_PREFIX])
                        + '...'
                        + repr(self._string[-STRING_SUMMARY_SUFFIX:])
                        + f'<{self._length}>')
            return repr(self._string)
        elif self._tag == TAG_INFIX:
            sym = self.infix_sym()
            return f'infix({sym}) in ' + self._container.summary(short=True)
        elif self._tag == TAG_CLOSURE:
            syms = self.closure_syms()
            if len(syms) > 1:
                sym = f'{syms[0]}, +{len(syms)-1}'
            else:
                sym = syms[0]
            return f'closure({sym})<{self.num_children}>'
        elif self._tag == TAG_CUSTOM:
            return f"custom {self._id}<{self._wosize}>"

        tag_part = self.tag_part()

        if short:
            if not tag_part:
                tag_part = 't0'
            return f'<{tag_part}:{self.num_children}>'

        if tag_part:
            tag_part += ':'

        contents = self.array_contents(short=True)

        return (f'({tag_part}' + ', '.join(contents) + ')')


    def field_summary(self, index, short=False):
        return (Value(self._value.field(index), self._target).
                summary(short))

    def __str__(self):
        if not self.valid:
            return '[invalid]'
        if self.immediate:
            return f'caml:{self.word // 2}'
        if self.debug is not None:
            return f'Caml:{self.debug}'

        color_char = colors(self._target).get(self._color_bits,
                                         f'BAD COLOR {self._color_bits}')[1]
        prefix = f'caml({color_char}):'

        if self._tag == TAG_DOUBLE:
            val = str(self._value.double_field(0))
            return f'{prefix}{val}'
        elif self._tag == TAG_STRING:
            if self._length > MAX_STRING_LEN:
                s = (repr(self._string[:STRING_PREFIX])
                     + '...' + repr(self._string[-STRING_SUFFIX:]))
            else:
                s = repr(self._string)
            return (f'{prefix}{s}<{self._length}>')
        elif self._tag == TAG_INFIX:
            sym = self.infix_sym()
            return (f'{prefix}infix({sym}) in'
                    + f' 0x{self._container._value.unsigned():x} '
                    + self._container.summary())
        elif self._tag == TAG_CLOSURE:
            syms = ', '.join(self.closure_syms())
            return (f'{prefix}closure({syms})'
                    + f' arity {self._functions[0][1]} ('
                    + ', '.join(self.field_summary(i + self._start_env)
                                for i in range(self.num_children))
                    + ')')
        elif self._tag == TAG_CUSTOM:
            return (f"{prefix}custom {self._id}"
                    f"<{self._wosize}>")

        tag_part = self.tag_part()
        if self._tag != 0:
            tag_part += ': '
        suffix = ('' if self.num_children <= MAX_BLOCK_SLOTS
                  else f'<{self.num_children}>')

        contents = self.array_contents()

        return (f'{prefix}({tag_part}'
                + ', '.join(contents)
                + f'){suffix}')

    # Useful in GDB and maybe one day in LLDB too.

    def child(self, index):
        if (not self.children) or index < 0 or index >= self.num_children:
            return
        if self._tag == TAG_DOUBLE_ARRAY:
            return self._value.double_field(index)
        elif self._tag == TAG_CLOSURE:
            return self._value.field(index + self._start_env).value()
        else:
            return self._value.field(index).value()

    # Useful in GDB and maybe one day in LLDB too.

    def child_array(self):
        """If the value is a block which can be regarded as an array,
        return the array as a debugger-native value."""
        if (not self.children):
            return
        if self._tag == TAG_DOUBLE_ARRAY:
            return self._value.double_array(self.num_children)
        elif self._tag == TAG_CLOSURE:
            return self._value.field_array(self._start_env, self.num_children)
        else:
            return self._value.field_array(0, self.num_children)

POOL_WSIZE = 4096

# `POOL_HEADER_WSIZE` is a compile-time constant (runtime/caml/sizeclasses.h)
# with no corresponding symbol for GDB to look up, so (like POOL_WSIZE
# above) it is hardcoded here and must be kept in sync with the runtime.
POOL_HEADER_WSIZE = 7

# ---------------------------------------------------------------------------
# Shared traversal helpers over the runtime's per-domain shared major heap.
#
# These mirror the linked-list/array structures maintained by
# runtime/shared_heap.c (`struct pool`, `struct large_alloc`,
# `struct caml_heap_state`, `pool_freelist`) and the domain table in
# runtime/domain.c (`struct dom_internal`, `all_domains`). They're used by
# both `Finder` (single-address lookup, below) and `HeapDump` (full
# heap-state dump).
# ---------------------------------------------------------------------------

def num_sizeclasses(target):
    "The number of size classes (`NUM_SIZECLASSES`), read from the target."
    pool_freelist = target.global_variable('pool_freelist').struct()
    return (pool_freelist['global_avail_pools'].type().size() //
            pool_freelist['global_avail_pools'].sub(0).type().size())


def iter_domains(target):
    """Yield `(index, dom_internal_struct)` for each active domain slot
    in `all_domains[0:caml_params->max_domains]`, skipping unused (NULL
    `state`) slots."""
    all_domains = target.global_variable('all_domains')
    caml_params = target.global_variable('caml_params').dereference().struct()
    max_domains = caml_params['max_domains'].unsigned()
    for i in range(max_domains):
        dom_internal = all_domains.pointer_index(i).struct()
        if dom_internal['state'].unsigned() == 0:
            continue
        yield i, dom_internal


def iter_pool_list(pool_list_head):
    """Yield each `pool*` value walking the `next` chain starting at
    `pool_list_head` (itself a `pool*` value)."""
    pool_list = pool_list_head
    while pool_list.unsigned():
        yield pool_list
        pool_list = pool_list.dereference().struct()['next']


def iter_pools_by_sizeclass(sizeclasses, pools_array):
    """Yield `(sizeclass, pool*)` for every pool in a
    `pool *pools[NUM_SIZECLASSES]` array."""
    for i in range(sizeclasses):
        pool_list = pools_array.sub(i)
        if pool_list.unsigned() == 0:
            continue
        for p in iter_pool_list(pool_list):
            yield i, p


def iter_large_list(large_list_head):
    """Yield `(large_alloc*, header_addr)` walking the `next` chain
    starting at `large_list_head`. `header_addr` is the address of the
    OCaml block header that immediately follows the `large_alloc`
    struct (i.e. one word before the corresponding OCaml value
    pointer — see `read_header` below)."""
    large_list = large_list_head
    while large_list.unsigned():
        base = large_list.unsigned()
        header_addr = base + large_list.dereference().type().size()
        yield large_list, header_addr
        large_list = large_list.dereference().struct()['next']


def read_header(target, header_addr):
    """Read and decode the header word located at `header_addr` (the
    address of the header itself, one word before the corresponding
    OCaml value pointer). Returns `(tag, color_bits, wosize)`."""
    val = target.value(header_addr + target.word_size)
    val_ptr = val.cast(val.type().pointer())
    hd = val_ptr.field(-1).unsigned()
    tag = hd & HEADER_TAG_MASK
    color_bits = hd & HEADER_COLOR_MASK
    wosize = hd >> HEADER_WOSIZE_SHIFT
    return tag, color_bits, wosize


class Finder:
    debug = False # Settable interactively from debugger.

    def __init__(self, target):
        self._sizeclasses = None
        self._wsize_sizeclass = None
        self._target = target

    def sizeclasses(self):
        if self._sizeclasses is None:
            self._sizeclasses = num_sizeclasses(self._target)
        return self._sizeclasses

    def wsize_sizeclass(self, sz):
        if self._wsize_sizeclass is None:
            self._wsize_sizeclass = (self._target.
                                     global_variable('wsize_sizeclass'))
        return self._wsize_sizeclass.sub(sz).unsigned()

    def _log(self, *args):
        if self.debug:
            print(*args)

    def _found(self, where):
        if self.debug:
            print(f"FOUND 0x{self.address:x} {where}")
        self.found.append(where)
        self.keep_going = self.debug

    def search_pool_list(self, description, pool_list):
        "Search a single pool list for `self.address`."
        count = 0
        for p in iter_pool_list(pool_list):
            if not self.keep_going:
                break
            count += 1
            base = p.unsigned()
            limit = base + POOL_WSIZE * self._target.word_size
            if base < self.address < limit:
                self._found(f"{description}: pool 0x{base:x}-0x{limit:x}")
        self._log(f"    searched {count} pools of {description}")

    def search_pools(self, description, pools):
        "Search an array `pool *pools[NUM_SIZECLASSES]` for `self.address`."
        self._log(f"  searching {description} pools")
        for i in range(self.sizeclasses()):
            pool_list = pools.sub(i)
            if pool_list.unsigned() == 0:
                continue
            self.search_pool_list(f"{description} "
                                  f"wsize={self.wsize_sizeclass(i)}",
                                  pool_list)
            if not self.keep_going:
                break

    def search_large(self, description, large_list):
        "Search a `large_alloc *` linked list for `self.address`."
        if large_list.unsigned() == 0:
            return
        count = 0
        for a, header_addr in iter_large_list(large_list):
            if not self.keep_going:
                break
            count += 1
            base = a.unsigned()
            _tag, _color, wosize = read_header(self._target, header_addr)
            limit = header_addr + (wosize + 1) * self._target.word_size
            if base < self.address < limit:
                self._found(f"{description} large "
                            f"0x{header_addr:x}-0x{limit:x}")
        self._log(f"  searched {count} large blocks of {description}")

    def search_heap(self, description, heap_state_p):
        "Searches a single `struct caml_heap_state *` for self.address."
        if heap_state_p.unsigned() == 0:
            self._log(f"shared heap for {description} is NULL")
            return
        heap_state = heap_state_p.dereference().struct()
        if self.keep_going:
            self.search_pools(f"{description} avail",
                              heap_state['avail_pools'])
        if self.keep_going:
            self.search_pools(f"{description} full",
                              heap_state['full_pools'])
        if self.keep_going:
            self.search_pools(f"{description} unswept avail",
                              heap_state['unswept_avail_pools'])
        if self.keep_going:
            self.search_pools(f"{description} unswept full",
                              heap_state['unswept_full_pools'])
        if self.keep_going:
            self.search_large(f"{description}",
                              heap_state['swept_large'])
        if self.keep_going:
            self.search_large(f"{description} unswept",
                              heap_state['unswept_large'])

    def search_domain(self, index, caml_state_p):
        "Search a single domain's heap for `self.address`."
        caml_state = caml_state_p.dereference().struct()
        young_start = caml_state['young_start'].unsigned()
        young_end = caml_state['young_end'].unsigned()
        description = f"domain {index}"
        self._log(f"searching {description}")
        if self.keep_going and (young_start <= self.address <= young_end):
                self._found(f"{description} minor heap "
                            f"0x{young_start:x}-0x{young_end:x}")
        if self.keep_going:
            self.search_heap(description, caml_state['shared_heap'])

    def find(self, expr, val):
        if not val.valid:
            print(f"{expr} not a valid expression")
            return
        if val.immediate:
            print(f"{expr} is immediate: {str(val)}")
            return
        if val.debug is not None:
            print(f"{expr} is a debug padding value: {val.debug}")
            return

        self.address = val.pointer.unsigned()
        mapping = self._target.mapping(self.address)
        if mapping:
            print(f"{expr} {str(val)} is from {mapping}, "
                  "not the heap.")
            return

        self.found = []
        self.keep_going = True

        # Search per-domain heaps.
        for i, dom_internal in iter_domains(self._target):
            self.search_domain(i, dom_internal['state'])
            if not self.keep_going:
                break

        # Global (orphaned) heap
        pool_freelist = self._target.global_variable('pool_freelist').struct()
        if self.keep_going:
            self.search_pools('global avail',
                              pool_freelist['global_avail_pools'])
        if self.keep_going:
            self.search_pools('global full',
                              pool_freelist['global_full_pools'])
        if self.keep_going:
            self.search_large("global",
                              pool_freelist['global_large'])

        if self.found:
            print(f"{expr} {str(val)}: 0x{self.address:x} found:")
            for where in self.found:
                print(f"  {where}")
        else:
            print(f"{expr} {str(val)} not found on heap")


# ---------------------------------------------------------------------------
# Full heap-state dump: every domain, every pool, optionally every block,
# plus the global orphan freelist and (where the target backend supports
# it) a per-OS-thread cross-check via thread-local storage.
#
# Unlike `Finder`, which stops as soon as it answers "does this address
# live on the heap", `HeapDump` walks everything and records it, for
# offline analysis (see tools/gdb_heap_dump_to_parquet.py).
# ---------------------------------------------------------------------------

class HeapDump:
    def __init__(self, target):
        self._target = target

    def dump(self, output_dir, full=False, label=None):
        """Walk every domain's shared heap plus the global orphan
        freelist, writing one JSONL file per record kind into
        `output_dir`: domains.jsonl, threads.jsonl, pools.jsonl,
        large.jsonl, global.jsonl, and (only when `full` is true)
        blocks.jsonl. Returns a dict of `{kind: record_count}`."""
        target = self._target
        self._n = num_sizeclasses(target)
        self._wsize = target.global_variable('wsize_sizeclass')
        self._wastage = target.global_variable('wastage_sizeclass')
        self._colors = colors(target)

        os.makedirs(output_dir, exist_ok=True)
        domains, pools, blocks, large, threads = [], [], [], [], []

        for index, dom_internal in iter_domains(target):
            domains.append(self._domain_record(index, dom_internal, pools,
                                                blocks, large, full))

        global_record = self._global_record(pools, blocks, large, full,
                                            label)

        # Cross-check: read the thread-local `caml_state` for every live
        # OS thread, if the target backend exposes thread enumeration
        # (currently only GDBTarget, via TLS). Dedup against `domains` is
        # done offline by `caml_state` pointer identity, not thread/pid —
        # see PLAN-GDB-IMPLEMENTATION.md.
        thread_iter = getattr(target, 'threads', None)
        if thread_iter is not None:
            for num, ptid, caml_state_addr in thread_iter():
                threads.append({
                    'thread_num': num,
                    'ptid': list(ptid) if ptid else None,
                    'caml_state': caml_state_addr,
                })

        counts = {
            'domains': self._write_jsonl(output_dir, 'domains', domains),
            'threads': self._write_jsonl(output_dir, 'threads', threads),
            'pools': self._write_jsonl(output_dir, 'pools', pools),
            'large': self._write_jsonl(output_dir, 'large', large),
            'global': self._write_jsonl(output_dir, 'global',
                                        [global_record]),
        }
        if full:
            counts['blocks'] = self._write_jsonl(output_dir, 'blocks',
                                                 blocks)
        return counts

    def _status_name(self, color_bits):
        return self._colors.get(color_bits,
                                (f'UNKNOWN(0x{color_bits:x})', '?'))[0]

    def _pool_records(self, list_name, owner_index, pools, out, full,
                      block_out):
        "Append one record per pool in a `pool*[NUM_SIZECLASSES]` array."
        for i, p in iter_pools_by_sizeclass(self._n, pools):
            fields = p.dereference().struct()
            addr = p.unsigned()
            out.append({
                'list': list_name,
                'owner_domain': owner_index,
                'address': addr,
                'sizeclass': i,
                'wsize': self._wsize.sub(i).unsigned(),
                'chunk': fields['chunk'].unsigned(),
                'chunk_size': fields['chunk_size'].unsigned(),
                'evacuate': bool(fields['evacuate'].signed()),
            })
            if full:
                self._block_records(addr, i, list_name, owner_index,
                                    block_out)

    def _block_records(self, pool_addr, sizeclass, list_name, owner_index,
                       out):
        """Walk one pool's blocks, mirroring `pool_sweep`'s own stepping
        logic exactly (runtime/shared_heap.c): a block whose header
        decodes as `No_scan_tag`/`NOT_MARKABLE` is a free run whose
        wosize counts the additional contiguous free blocks that follow
        it; anything else is a live/unmarked/garbage block occupying
        exactly one slot."""
        target = self._target
        word_size = target.word_size
        wh = self._wsize.sub(sizeclass).unsigned()
        wastage = self._wastage.sub(sizeclass).unsigned()
        p_addr = pool_addr + (POOL_HEADER_WSIZE + wastage) * word_size
        end_addr = pool_addr + POOL_WSIZE * word_size
        wh_bytes = wh * word_size
        while p_addr + wh_bytes <= end_addr:
            tag, color_bits, wosize = read_header(target, p_addr)
            free = (tag == NO_SCAN_TAG and color_bits == NOT_MARKABLE)
            out.append({
                'list': list_name,
                'owner_domain': owner_index,
                'pool': pool_addr,
                'sizeclass': sizeclass,
                'header_address': p_addr,
                'address': p_addr + word_size,
                'tag': tag,
                'tag_name': 'Free' if free else TAGS.get(tag, f't{tag}'),
                'status': 'free' if free else self._status_name(color_bits),
                'wosize': wosize,
                'free_run_blocks': (wosize + 1) if free else None,
            })
            p_addr += wh_bytes * ((wosize + 1) if free else 1)

    def _large_records(self, list_name, owner_index, large_list_head, out):
        for _, header_addr in iter_large_list(large_list_head):
            tag, color_bits, wosize = read_header(self._target, header_addr)
            out.append({
                'list': list_name,
                'owner_domain': owner_index,
                'header_address': header_addr,
                'address': header_addr + self._target.word_size,
                'tag': tag,
                'tag_name': TAGS.get(tag, f't{tag}'),
                'status': self._status_name(color_bits),
                'wosize': wosize,
            })

    def _pool_list_count(self, pools):
        return sum(1 for _ in iter_pools_by_sizeclass(self._n, pools))

    def _thread_id(self, dom_internal):
        # `tid` is a `pthread_t` — opaque on some platforms, so don't let
        # a failure to read it as a scalar abort the whole dump.
        try:
            return dom_internal['tid'].unsigned()
        except Exception:
            return None

    def _domain_record(self, index, dom_internal, pools, blocks, large,
                       full):
        caml_state_p = dom_internal['state']
        caml_state = caml_state_p.dereference().struct()
        heap_state_p = caml_state['shared_heap']
        record = {
            'index': index,
            'id': caml_state['id'].signed(),
            'unique_id': caml_state['unique_id'].signed(),
            'caml_state': caml_state_p.unsigned(),
            'tid': self._thread_id(dom_internal),
            'young_start': caml_state['young_start'].unsigned(),
            'young_ptr': caml_state['young_ptr'].unsigned(),
            'young_end': caml_state['young_end'].unsigned(),
            'marking_done': caml_state['marking_done'].unsigned(),
            'sweeping_done': caml_state['sweeping_done'].unsigned(),
            'allocated_words': caml_state['allocated_words'].unsigned(),
            'swept_words': caml_state['swept_words'].unsigned(),
            'shared_heap': heap_state_p.unsigned(),
            'unswept_pool_count': None,
        }
        if heap_state_p.unsigned() == 0:
            return record

        heap_state = heap_state_p.dereference().struct()
        stats = heap_state['stats'].struct()
        record.update({
            'pool_words': stats['pool_words'].signed(),
            'pool_live_words': stats['pool_live_words'].signed(),
            'pool_live_blocks': stats['pool_live_blocks'].signed(),
            'pool_frag_words': stats['pool_frag_words'].signed(),
            'large_words': stats['large_words'].signed(),
            'large_blocks': stats['large_blocks'].signed(),
        })

        for list_name, key in (('avail', 'avail_pools'),
                               ('full', 'full_pools'),
                               ('unswept_avail', 'unswept_avail_pools'),
                               ('unswept_full', 'unswept_full_pools')):
            self._pool_records(list_name, index, heap_state[key], pools,
                               full, blocks)

        unswept_large = heap_state['unswept_large'].unsigned() != 0
        record['unswept_pool_count'] = (
            self._pool_list_count(heap_state['unswept_avail_pools']) +
            self._pool_list_count(heap_state['unswept_full_pools']) +
            (1 if unswept_large else 0))

        self._large_records('swept', index, heap_state['swept_large'],
                            large)
        self._large_records('unswept', index, heap_state['unswept_large'],
                            large)
        return record

    def _global_record(self, pools, blocks, large, full, label):
        target = self._target
        pool_freelist = target.global_variable('pool_freelist').struct()
        self._pool_records('orphan_avail', None,
                           pool_freelist['global_avail_pools'], pools, full,
                           blocks)
        self._pool_records('orphan_full', None,
                           pool_freelist['global_full_pools'], pools, full,
                           blocks)
        self._large_records('orphan', None, pool_freelist['global_large'],
                            large)
        stats = pool_freelist['stats'].struct()
        return {
            'label': label,
            'current_chunk': pool_freelist['current_chunk'].unsigned(),
            'current_chunk_size':
                pool_freelist['current_chunk_size'].unsigned(),
            'chunk_words': pool_freelist['chunk_words'].unsigned(),
            'max_chunk_words': pool_freelist['max_chunk_words'].unsigned(),
            'active_pools': pool_freelist['active_pools'].unsigned(),
            'fresh_pools': pool_freelist['fresh_pools'].unsigned(),
            'free_pools': sum(1 for _ in
                              iter_pool_list(pool_freelist['free'])),
            'orphan_pool_live_words': stats['pool_live_words'].signed(),
            'orphan_pool_live_blocks': stats['pool_live_blocks'].signed(),
            'orphan_large_blocks': stats['large_blocks'].signed(),
            'compactions_count': target.global_variable(
                'caml_compactions_count').unsigned(),
        }

    @staticmethod
    def _write_jsonl(output_dir, name, records):
        path = os.path.join(output_dir, f'{name}.jsonl')
        with open(path, 'w') as f:
            for r in records:
                f.write(json.dumps(r) + '\n')
        return len(records)


def check_heap(target):
    """Check that every domain's unswept pool/large lists are empty — the
    invariant that should hold right after a full sweep, e.g. at a
    major-GC-cycle boundary (see runtime/shared_heap.c: caml_sweep).
    Returns `(all_ok, [{'index', 'unswept_avail_pools',
    'unswept_full_pools', 'unswept_large', 'ok'}, ...])`, one entry per
    domain that has an initialised shared heap."""
    n = num_sizeclasses(target)
    results = []
    all_ok = True
    for index, dom_internal in iter_domains(target):
        caml_state = dom_internal['state'].dereference().struct()
        heap_state_p = caml_state['shared_heap']
        if heap_state_p.unsigned() == 0:
            continue
        heap_state = heap_state_p.dereference().struct()
        avail = sum(1 for _ in iter_pools_by_sizeclass(
            n, heap_state['unswept_avail_pools']))
        full = sum(1 for _ in iter_pools_by_sizeclass(
            n, heap_state['unswept_full_pools']))
        large = heap_state['unswept_large'].unsigned() != 0
        ok = (avail == 0 and full == 0 and not large)
        all_ok = all_ok and ok
        results.append({
            'index': index,
            'unswept_avail_pools': avail,
            'unswept_full_pools': full,
            'unswept_large': large,
            'ok': ok,
        })
    return all_ok, results
