import z3
from .esilclasses import *
from .esilregisters import *
from .esilmemory import *
from .esilprocess import *

import re # for buffer constraint
all_bytes = "".join([chr(x) for x in range(256)])

class ESILState:
    
    def __init__(self, r2api, **kwargs):
        self.kwargs = kwargs
        self.r2api = r2api
        self.pure_symbolic = kwargs.get("sym", False)

        if kwargs.get("opt", False):
            self.solver = z3.Optimize()
        else:
            self.solver = z3.SimpleSolver()

        #self.constraints = []
        self.model = None

        self.esil = {"cur":0, "old":0, "stack":[]}
        self.stack = self.esil["stack"]
        self.info = self.r2api.get_info()
        self.debug = kwargs.get("debug", False)
        self.trace = kwargs.get("trace", False)

        self.memory = {}
        self.registers = {}
        self.aliases = {}
        self.condition = None

        # steps executed and distance from goal
        self.steps = 0
        self.distance = 0xffffffff
        self.target = None

        if "info" in self.info:
            self.bits = self.info["info"]["bits"]
            self.endian = self.info["info"]["endian"]
        else:
            self.bits = 64
            self.endian = "little"

        if kwargs.get("init", True):
            self.proc = ESILProcess(r2api, **kwargs)
            self.init_state()

    def init_state(self):
        # get information about the registers and memory
        self.init_registers()
        self.init_memory()

    def init_memory(self):
        self.memory = ESILMemory(self.r2api, self.info, self.pure_symbolic)
        self.memory.solver = self.solver
        self.memory.init_memory()

    def init_registers(self):
        self.register_info = self.r2api.get_register_info()
        self.aliases = {}
        registers = self.register_info["reg_info"]
        aliases = self.register_info["alias_info"]
        register_values = self.r2api.get_all_registers()

        for alias in aliases:
            self.aliases[alias["role_str"]] = alias

        for register in registers:
            register["value"] = register_values[register["name"]]

        self.registers = ESILRegisters(registers, self.aliases, sym=self.pure_symbolic)
        self.registers.init_registers()

    def set_symbolic_register(self, name, var=None):
        if var == None:
            var = name

        size = self.registers[name].size()
        self.registers[name] = z3.BitVec(var, size)

    def constrain(self, *constraints):
        #self.constraints.extend(constraints)
        self.solver.add(*constraints)

    # this bizarre function takes a regular expression like [A-Z 123]
    # and constrains all the bytes in the bv to fit the expression
    def constrain_bytes(self, bv, regex):

        # if its a bytes expr just constrain beginning to those values
        if type(regex) == bytes:
            for i in range(len(regex)):
                self.constrain(z3.Extract(7+i*8, i*8, bv) == regex[i])

            return 

        if z3.is_bv(bv):
            bv = [z3.Extract(b*8+7, b*8, bv) for b in range(int(bv.size()/8))]

        # this is gross and could probably break
        opts = []
        new_regex = regex[:]
        negate = False
        if len(regex) > 2 and regex[:2] == "[^":
            negate = True
            new_regex = new_regex.replace("[^", "[")

        dashes = [i for i,c in enumerate(regex) if c == "-"]
        for d in dashes:
            if regex[d-1] != "\\" and len(regex) > d:
                x = ord(regex[d-1])
                y = ord(regex[d+1])
                opts.append([x, y])
                new_regex = new_regex.replace(regex[d-1:d+2], "")
        
        vals = []
        if new_regex != "[]":
            vals = [ord(x) for x in re.findall(new_regex, all_bytes, re.DOTALL)]
            
        for b in bv:
            or_vals = []
            for val in vals:
                or_vals.append(b == val)

            for opt in opts:
                or_vals.append(z3.And(b >= opt[0], b <= opt[1]))

            if negate:
                self.constrain(z3.Not(z3.Or(*or_vals)))
            else:
                self.constrain(z3.Or(*or_vals))

    def constrain_register(self, name, val):
        reg = self.registers[name]
        self.constrain(reg == val)

    def evaluate_register(self, name, eval_type="eval"):
        val = self.registers[name]

        if eval_type == "max":
            self.solver.maximize(val)
        elif eval_type == "min":
            self.solver.minimize(val)

        if self.model == None:
            sat = self.solver.check()
            
            if sat == z3.sat:
                self.model = self.solver.model()
            else:
                raise ESILUnsatException("state has unsatisfiable constraints")

        value = self.model.eval(val, True)

        return value

    def evaluate(self, val):
        sat = self.solver.check()
        
        if sat == z3.sat:
            model = self.solver.model()
        else:
            raise ESILUnsatException("state has unsatisfiable constraints")

        value = model.eval(val, True)
        return value

    # shortcut to eval + constrain
    def evalcon(self, val):
        eval_val = self.evaluate(val)
        self.constrain(val == eval_val)
        return eval_val

    def eval_max(self, sym, n=16):
        solutions = []

        while len(solutions) < n:

            self.solver.push()
            for sol in solutions:
                self.solver.add(sym != sol)

            satisfiable = self.solver.check()

            if satisfiable == z3.sat:
                m = self.solver.model()
                solutions.append(m.eval(sym, True))
            else:
                self.solver.pop()
                break

            self.solver.pop()

        return solutions

    def evaluate_buffer(self, bv):
        buf = self.evaluate(bv)
        val = buf.as_long()
        length = int(bv.size()/8)
        return bytes([(val >> (8*i)) & 0xff for i in range(length)])

    def evaluate_string(self, bv):
        b = self.evaluate_buffer(bv)
        if b"\x00" in b:
            null_ind = b.index(b"\x00")
            b = b[:null_ind]

        return b.decode()
        
    def step(self):
        pc = self.registers["PC"].as_long() 
        instr = self.r2api.disass(pc)
        new_states = self.proc.execute_instruction(self, instr)
        return new_states

    def is_sat(self):
        if self.solver.check() == z3.sat:
            return True
        
        return False

    # apply the ES state to the r2pipe ESIL VM
    def apply(self):
        # apply registers
        for reg in self.registers._registers:
            if not self.registers._registers[reg]["sub"]:
                register = self.registers[reg]
                value = self.evaluate(register)
                self.constrain(register == value)
                self.r2api.set_reg_value(reg, value.as_long())

        # apply memory
        for addr in self.memory._memory:
            value_bv = self.evaluate(self.memory[addr])
            self.constrain(self.memory[addr] == value_bv)

            value = self.evaluate_buffer(self.memory[addr])
            length = int(self.memory[addr].size()/8)

            self.r2api.write(addr, value, length)

    def clone(self):
        clone = self.__class__(
            self.r2api, 
            init=False, 
            sym=self.pure_symbolic, 
            debug=self.debug, 
            trace=self.trace
        )

        clone.stack = self.stack[:]
        clone.constrain(*self.solver.assertions())

        clone.proc = self.proc #.clone() no need to clone this 
        clone.steps = self.steps
        clone.distance = self.distance
        clone.registers = self.registers.clone()
        clone.memory = self.memory.clone()
        clone.memory.solver = clone.solver

        return clone

class ESILStateManager:

    def __init__(self, active=[], avoid=[], lazy=False):
        self.active = set(active)
        self.inactive = set()
        self.unsat = set()
        self.recently_added = set()

        if isinstance(avoid, int):
            avoid = [avoid]

        self.avoid = avoid
        self.cutoff = 32

        self.lazy = lazy

    def next(self):
        #print(self.active, self.inactive)

        if len(self.active) == 0:
            return
        elif len(self.active) > self.cutoff:
            state = max(self.active, key=lambda s: s.steps)
        else:
            state = min(self.active, key=lambda s: s.steps)
            #state = min(self.active, key=lambda s: s.distance) 

        #print(state.distance)
        self.active.discard(state)
        return state

    def add(self, state):
        pc = state.registers["PC"]
        if z3.is_bv_value(pc):
            if pc.as_long() in self.avoid:
                self.inactive.add(state)
            else:
                self.active.add(state)

        elif self.lazy or state.is_sat():
            self.active.add(state)

        else:
            self.unsat.add(state)

    def entry_state(self, r2api, **kwargs):
        state = ESILState(r2api, **kwargs)
        self.add(state)
        return state