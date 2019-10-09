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

        if state != None:
            self.state = state
            self.bits = state.info["info"]["bits"]

            self.registers = state.registers
            self.memory = state.memory
    
    def isConditional(self):
        return (self.word[0] == "?")

    def isOperator(self):
        return (self.word in esilops.opcodes)

    def isLiteral(self):
        return (self.word.isdigit() or self.word[:2] == "0x")

    def isRegister(self):
        return (self.word in self.registers)

    def getRegister(self):
        register = self.registers[self.word]
        return register

    def getLiteralValue(self):
        if(self.word.isdigit()):
            return int(self.word)
        elif self.word[:2] == "0x":
            return int(self.word, 16)

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
    def __init__(self, r2p=None, init=True, debug=False):
        self.debug = debug
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

    def run(self, state, target=None):
        # if target is None exec until ret
        if target == None:
            find = lambda x, s: x["opcode"] == "ret"
        elif type(target) == int:
            find = lambda x, s: x["offset"] == target

        found = False

        while not found:
            instr = self.r2api.disass()[0]
            found = find(instr, state)

            if not found:
                self.parseExpression(instr["esil"], state)
                self.r2api.step(instr["size"])
    
    def initState(self):
        if len(self.states) > 0:
            return self.states[0]

        state = ESILState(self.r2api)
        self.states.append(state)
        return state

    def parseExpression(self, expression, state):

        if self.debug:
            print("expr: %s" % expression)

        stack = state.stack

        if "?" in expression:
            expression = self.parseConditionals(expression)
            
        words = expression.split(",")

        for word_str in words:
            word = ESILWord(word_str, state)

            if word.isConditional():
                self.doConditional(word)

            elif word.isOperator():
                word.doOp(stack)

            else:
                stack.append(word.getPushValue())

    def parseConditionals(self, expression):
        conditionals = re.findall(r"\?\{(.*?)\}", expression)

        for cond in conditionals:
            ident = "?[%d]" % self.cond_count
            self.conditionals[ident] = cond
            self.cond_count += 1

            expression = expression.replace("?{%s}" % cond, ident, 1)

        return expression
        
    # TODO: change this logic
    def doConditional(self, word):
        return

        raise ESILUnimplementedException

        #if self.popAndEval():
        #    self.parseExpression(self.conditionals[word.word])
