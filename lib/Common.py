from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from clang.cindex import Cursor, CursorKind, StorageClass, TypeKind, Type
from .LogSet import logSetup
from .CodeAnalyzer import CodeAnalyzer
from .AnalyzerHelper import (
    PreorderAST,  # 用于遍历AST, 且可控制是否继续
    is_arrIdxNode,
    is_callFuncNode,
    is_callFuncParam,
    is_callGlobalVarNode,
    is_callLocalVarNode,
    is_callVarNode,
    is_globalVarDeclNode,
    is_localVarDeclNode,  #
    is_varAssignNode,  # x = y
    is_forLoopNode,  # for(i = 0; i<x; ++i)
    is_whileLoopNode,  # while(n < 100)
    is_returnStatement,
    is_parameterRefNode,
    #
    is_funcDecl,
    is_funcDef,
    #
    is_static,
    is_array,
    is_pointer,
    is_struct,
    is_union,
    is_func_pointer,
    #
    nodeName,
    typeName,
    pureTypeName,
)
from enum import Enum, auto

logger = logSetup()


# %% 数据类型
@dataclass(frozen=True)
class CodeLocation:
    # 代码位置
    file: Path
    begin: int = None
    end: int = None

    @classmethod
    def from_cursor(cls, node: Cursor):
        if node.location and node.location.file and node.extent and node.extent.start:
            return CodeLocation(
                file=Path(node.location.file.name).resolve(),
                begin=node.extent.start.line,
                end=node.extent.end.line,
            )

        return None


class TypeCategory(Enum):
    """
    类型分类
    """

    built_in = auto()  # 内置算术类型
    struct = auto() # 结构体
    union = auto() # 联合体
    array = auto() # 数组
    func_pointer = auto() # 函数指针
    pointer = auto() # 普通指针

    @classmethod
    def from_cursor(clc, node: Cursor | Type):
        if is_struct(node):
            return TypeCategory.struct
        if is_union(node):
            return TypeCategory.union
        if is_array(node):
            return TypeCategory.array
        if is_func_pointer(node):
            return TypeCategory.func_pointer
        if is_pointer(node):
            return TypeCategory.pointer

        return TypeCategory.built_in


@dataclass
class VarNode:
    """
    通用变量节点
    """

    name: str
    type_name: str = None
    pure_type_name: str = None
    var_category: TypeCategory = TypeCategory.built_in
    is_member: bool = False

    # TODO member_list 是否应该改为dict类型?, 成员类型为
    member_list: list["VarNode"] | list["FuncBase"] = None
    pass


@dataclass
class Parameter(VarNode):
    """
    函数形参节点
    """

    position: int = None  # 参数在函数参数列表中的位置, 从0开始计算

    # TODO 是否需要related_with_func 即使和函数有关, 也是和函数返回值有关?
    related_with_func: set[str] = field(default_factory=set())
    related_with_var: set[str] = field(default_factory=set())

    @classmethod
    def from_cursor(cls, node: Cursor, position: int = 0):
        """
        node: 单个形参声明的子节点
        position: 形参位置
        """

        pass


@dataclass
class ReturnValNode(VarNode):
    """
    函数返回值节点
    """

    name: str | None = None  # 不再是变量名, 而是函数的名字, 或默认为None
    type_name: str = "void"  # 类型名称, 默认为void
    pure_type_name: str = "void"  # 类型纯粹名称

    is_void: bool = True  # 是否为void

    # TODO 是否需要related_with_func 即使和函数有关, 也是和函数返回值有关?
    related_with_func: set[str] = field(default_factory=set())
    related_with_var: set[str] = field(default_factory=set())

    @classmethod
    def from_cursor(cls, node: Cursor):
        """
        从一个函数声明/定义节点得到
        """
        return_type: Type = node.result_type

        cur_node = ReturnValNode(
            name=None,
            type_name=typeName(return_type),
            pure_type_name=pureTypeName(return_type),
            var_category=TypeCategory.from_cursor(return_type),
            is_void=(return_type.get_canonical() == "void"),
        )
        memberListParseRecursive(cur_node, return_type)

        return cur_node

@dataclass
class FuncBase:
    # 返回值和形参
    return_type: str = None
    return_var: ReturnValNode = field(default_factory=ReturnValNode)
    parameters: list[Parameter] = field(default_factory=[])


@dataclass
class FuncPointee(FuncBase, VarNode):
    """
    函数指针所指向的节点 信息类
    """

    # 被用什么函数赋值
    assigned_func: set[str] = field(default_factory=set())
    # assigned_func: set[FuncNode] = field(default_factory=set())
    # 被其他的函数指针修改, 如 *funcp1 = func_arr[2];
    changed_var: set[str] = field(default_factory=set())


@dataclass
class FuncNode(FuncBase):
    """
    函数定义/声明 信息类
    存储函数的完整信息，包括声明/定义位置、参数、调用关系等
    TODO 可能需要改为可hash的
    """

    name: str = None
    decl_location: CodeLocation = None  # 函数声明的代码位置
    def_location: CodeLocation = None  # 函数定义的代码位置

    use_arr = False  # 在函数体中是否使用了数组, 若形参是数组但不使用则不考虑
    access_global_arr_element = False  # 是否在函数体中访问了全局数组

    # 该函数调用的其他函数名称列表
    called_func: set[str] = field(default_factory=set())
    # 该函数调用的全局变量名称列表
    called_var: set[str] = field(default_factory=set())
    # 该函数修改的全局变量名称列表
    change_var: set[str] = field(default_factory=set())

    @classmethod
    def from_cursor(cls, node: Cursor):
        """
        从函数定义的Cursor中构建FuncNode
        这个可能不应该实现
        """
        raise NotImplementedError


# @dataclass
# class FuncPointerNode:
#     name: str  # 函数名称
#     return_type: str = None
#
#     # 返回值和形参
#     return_var: ReturnValNode = field(default_factory=ReturnValNode)
#     parameters: list[Parameter] = field(default_factory=[])
#
#     # TODO 这个结构很难


@dataclass
class GlobalVarNode(VarNode):
    """
    全局变量节点信息类
    存储全局变量（包括静态全局变量）的完整信息
    """

    decl_location: CodeLocation = None  # 变量声明位置
    def_location: CodeLocation = None  # 变量定义位置

    is_static: bool = False  # 是否为static变量

    changed_in_func: set[str] = field(default_factory=set())  # 在哪些函数中被修改
    related_with_var: set[str] = field(default_factory=set())  # 和哪些全局变量的值有关

    @classmethod
    def from_cursor(cls, node: Cursor):
        """
        从clang的Cursor对象创建GlobalVarNode实例
        :param node: clang解析得到的游标对象
        :return: GlobalVarNode实例
        """
        return CodeAnalyzer.process_globalVarNode(node)


@dataclass
class LocalVarNode(VarNode):
    """
    局部变量节点
    """

    is_static: bool = False  # 是否为static变量
    in_func: str = None  # 所在的函数名称

    @classmethod
    def from_cursor(cls, node: Cursor):
        pass


def memberListParseRecursive(
    varNode: GlobalVarNode | LocalVarNode | Parameter | ReturnValNode, NodeType: Type
):
    """
    递归为 VarNode 的 member_list 填充信息, 如果是数组或指针, 它的member_list将是一个变量点, 名为 "arr_element" (对于数组), "point_element" (对于指针)

    VarNode: 变量信息节点
    NodeType: 变量的clangd index 类型

    # TODO 还未处理 void* 这种情况? 似乎已经包含了这总情况
    """
    regular_type: Type = NodeType.get_canonical()  # 规范化类型

    if TypeCategory.from_cursor(regular_type) == TypeCategory.built_in:
        return

    # 当前节点的成员节点 应该是相同类型?
    if isinstance(varNode, VarNode):
        cur_py_type = type(varNode)
    else:
        raise TypeError(f"Unsupported varNode type: {type(varNode)}")

    # if isinstance(VarNode, GlobalVarNode):
    #     cur_py_type = GlobalVarNode
    # elif isinstance(VarNode, LocalVarNode):
    #     cur_py_type = LocalVarNode
    # elif isinstance(VarNode, Parameter):
    #     cur_py_type = Parameter
    # elif isinstance(VarNode, ReturnValNode):
    #     cur_py_type = ReturnValNode
    # else:
    #     raise TypeError(f"Unsupported VarNode type: {type(VarNode)}")

    # 针对数组和指针的情况
    if is_array(regular_type) or is_pointer(regular_type):
        # 褪去一层指针或数组后的类型
        if is_array(regular_type):
            member_name = "arr_element"
            member_type: Type = regular_type.get_array_element_type()
        else:
            member_name = "point_element"
            member_type = regular_type.get_pointee()

        member_type_name = member_type.spelling
        member_pure_type_name = pureTypeName(member_type)
        member_node = cur_py_type(
            name=member_name,
            type_name=member_type_name,
            pure_type_name=member_pure_type_name,
            var_category=TypeCategory.from_cursor(member_type),
            is_member=True,
        )

        if isinstance(varNode, ReturnValNode):
            member_node.is_void = member_type_name == "void"

        memberListParseRecursive(member_node, member_type)
        varNode.member_list = [member_node]

    # 针对结构体和枚举类型的情况
    elif is_struct(regular_type) or is_union(regular_type):
        type_def_node: Cursor = regular_type.get_declaration()

        varNode.member_list = []
        for member_decl_node in type_def_node.get_children():
            member_name = member_decl_node.spelling
            member_type = member_decl_node.type
            member_type_name = member_type.spelling
            member_pure_type_name = pureTypeName(member_decl_node)
            member_node = cur_py_type(
                name=member_name,
                type_name=member_type_name,
                pure_type_name=member_pure_type_name,
                var_category=TypeCategory.from_cursor(member_type),
                is_member=True,
            )
            memberListParseRecursive(member_node, member_type)
            varNode.member_list.append(member_node)


# 全局变量字典：键为 文件路径(相对于项目路径)/变量名，值为GlobalVarNode对象
GlobalVarDict: defaultdict[str, GlobalVarNode] = defaultdict()

# 函数字典：键为函数名，值为FuncNode对象
FuncDict: defaultdict[str, FuncNode] = defaultdict()

