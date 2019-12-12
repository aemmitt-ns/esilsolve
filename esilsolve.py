from r2api import R2API
import solver
import esilops
import json
from esilclasses import * 
from esilregisters import *
from esilmemory import *
from esilstate import *
import re

import logging

class ESILWord:
    def __init__(self, word=None, state=None):
        self.word = word
        self.len = len(word)

        if state != None:
            self.state = state
            self.bits = state.info["info"]["bits"]

            self.registers = state.registers
            self.memory = state.memory
    
    def isIf(self):
        return (self.len > 0 and self.word[0] == "?")

    def isElse(self):
        return (self.word == "}{")

    def isEndIf(self):
        return (self.word == "}")

    def isOperator(self):
        return (self.word in esilops.opcodes)

    def isLiteral(self):
        if (self.word.isdigit() or (self.len > 2 and self.word[:2] == "0x")):
            return True
        elif self.len > 1 and self.word[0] == "-" and self.word[1:].isdigit():
            return True
        else: 
            return False

    def isRegister(self):
        return (self.word in self.registers)

    def getRegister(self):
        #register = self.registers[self.word]
        return self.word

    def getLiteralValue(self):
        if(self.word.isdigit()):
            return int(self.word)
        elif self.len > 2 and self.word[:2] == "0x":
            return int(self.word, 16)
        elif self.len > 1 and self.word[0] == "-" and self.word[1:].isdigit():
            return int(self.word)

    def getPushValue(self):
        if(self.isLiteral()):
            val = self.getLiteralValue()
            return val

        elif(self.isRegister()):
            return self.getRegister()

        else:
            raise esilops.ESILUnimplementedException

    def doOp(self, stack):
        op = esilops.opcodes[self.word]
        op(self.word, stack, self.state)


class ESILSolver:
    def __init__(self, r2p=None, init=True, debug=False, trace=False):
        self.debug = debug
        self.trace = trace
        self.states = []

        self.conditionals = {}
        self.cond_count = 0

        if r2p == None:
            r2api = R2API()
        else:
            r2api = R2API(r2p)

        self.r2api = r2api
        self.didInitVM = False
        self.info = self.r2api.getInfo()

        if init:
            self.initState()

    # initialize the ESIL VM
    def initVM(self):
        self.r2api.initVM()
        self.didInitVM = True

    def run(self, state=None, target=None):
        # if target is None exec until ret
        if target == None:
            find = lambda x, s: x["opcode"] == "ret"
        elif type(target) == int:
            find = lambda x, s: x["offset"] == target

        found = False

        states = self.states
        if state != None:
            states = [state]

        while not found:
            for state in states:
                pc = state.registers["PC"].as_long() 
                instr = self.r2api.disass(pc)
                found = find(instr, state)

                if not found:
                    self.executeInstruction(state, instr)
    
    def executeInstruction(self, state, instr):
        if self.debug:
            print("\nexpr: %s" % instr["esil"])
            print("opcode: %s" % instr["opcode"])

        # pc should never be anything other than a BitVecVal
        old_pc = state.registers["PC"].as_long() 
        self.parseExpression(instr["esil"], state)
        new_pc = state.registers["PC"].as_long()

        if new_pc == old_pc:
            state.registers["PC"] = old_pc + instr["size"]

        if self.trace:
            self.r2api.emustep()
            self.traceRegisters(state)

    def initState(self):
        if len(self.states) > 0:
            return self.states[0]

        state = ESILState(self.r2api)
        self.states.append(state)
        return state

    def addState(self, state):
        print("adding state...")
        r2tmp = state.r2api
        state.r2api = None
        new_state = deepcopy(state)
        new_state.r2api = r2tmp
        self.states.append(new_state)

    def parseExpression(self, expression, state):

        stack = state.stack            
        words = expression.split(",")

        execute = True
        for word_str in words:
            word = ESILWord(word_str, state)

            if execute and word.isIf():
                execute = self.doIf(word, state)

            elif word.isElse():
                execute = not execute

            elif word.isEndIf():
                execute = True

            elif execute:
                if word.isOperator():
                    word.doOp(stack)
                else:
                    stack.append(word.getPushValue())
        
    def doIf(self, word, state):
        val = state.stack.pop()

        # this should not be necessary but it is
        # i really need to figure out what is happening here
        print(val)
        zero = 0
        if solver.is_bv(val):
            #zero = solver.BitVecVal(0, val.size())
            val = solver.BV2Int(val)
        
        state.solver.push()
        cond = val == zero
        state.solver.add(cond)
        sat = state.solver.check()
        
        if sat == solver.sat:
            return False

        state.solver.pop()
        cond = val != zero
        state.solver.add(cond)    
        return True

    def traceRegisters(self, state):
        for regname in state.registers._registers:
            register = state.registers._registers[regname]
            #print(regname, reg_value)
            if register["type_str"] in ["gpr", "flg"]:
                emureg = self.r2api.getRegValue(register["name"])
                try:
                    reg_value = solver.simplify(state.registers[regname])
                    #if reg_value.as_long() != emureg:
                    print("%s: %s , %s" % (register["name"], reg_value, emureg))
                except Exception as e:
                    #print(e)
                    pass