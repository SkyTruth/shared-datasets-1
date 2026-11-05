"""Evaluate the CEL subset emitted by WIF policies, without cloud credentials.

The grammar rejects unsupported syntax. Evaluate the actual Terraform-rendered
expressions, including lazy conditionals and missing-claim errors, rather than
reimplementing the authorization policy as Python predicates.
"""
import ast
import re

from lark import Lark, Token


CEL = Lark(r"""
?start: choice
?choice: disjunction | disjunction "?" choice ":" choice -> choice
?disjunction: conjunction | disjunction "||" conjunction -> either
?conjunction: comparison | conjunction "&&" comparison -> both
?comparison: addition | addition "==" addition -> equal
           | addition "in" addition -> contains
?addition: access | addition "+" access -> add
?access: atom | access "." NAME -> field
       | access "." "matches" "(" choice ")" -> matches
       | access "." "startsWith" "(" choice ")" -> starts_with
       | access "." "map" "(" NAME "," choice ")" -> map_values
?atom: "(" choice ")" | STRING -> string
     | "[" [choice ("," choice)*] "]" -> array
     | "has" "(" choice ")" -> has
     | NAME -> variable
STRING: /"([^"\\]|\\.)*"|'([^'\\]|\\.)*'/
%import common.CNAME -> NAME
%import common.WS
%ignore WS
""", parser="lalr", maybe_placeholders=False)


def cel(expression, claims):
    def evaluate(tree, scope):
        if isinstance(tree, Token):
            return str(tree)
        args = tree.children
        match tree.data:
            case "string":
                return ast.literal_eval(str(args[0]))
            case "variable":
                return scope[str(args[0])]
            case "field":
                return evaluate(args[0], scope)[str(args[1])]
            case "has":
                try:
                    evaluate(args[0], scope)
                    return True
                except KeyError:
                    return False
            case "choice":
                return evaluate(args[1] if evaluate(args[0], scope) else args[2], scope)
            case "either":
                return evaluate(args[0], scope) or evaluate(args[1], scope)
            case "both":
                return evaluate(args[0], scope) and evaluate(args[1], scope)
            case "array":
                return [evaluate(value, scope) for value in args]
            case "map_values":
                return [evaluate(args[2], {**scope, str(args[1]): value})
                        for value in evaluate(args[0], scope)]
            case "matches":
                return re.search(evaluate(args[1], scope), evaluate(args[0], scope)) is not None
            case "starts_with":
                return evaluate(args[0], scope).startswith(evaluate(args[1], scope))
            case "equal":
                return evaluate(args[0], scope) == evaluate(args[1], scope)
            case "contains":
                return evaluate(args[0], scope) in evaluate(args[1], scope)
            case "add":
                return evaluate(args[0], scope) + evaluate(args[1], scope)
            case _:
                raise AssertionError(tree)
    return evaluate(CEL.parse(expression), {"assertion": claims})
