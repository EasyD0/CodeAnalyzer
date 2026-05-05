# %% 帮助函数
from typing import overload

from clang.cindex import Cursor, CursorKind, StorageClass, Type, TypeKind
from typing import Callable, Any, Iterable


def PreorderAST(
    root: Any,
    process_node_func: Callable[[Any], bool],
    get_children: Callable[[Any], Iterable[Any]],
) -> None:
    """
    前序遍历 AST 或树形结构，并在处理当前节点后决定是否跳过其子树。

    :param root: 当前节点（根节点）
    :param process_node_func: 处理当前节点的函数，返回 True 表示继续遍历子树，False 表示跳过子树
    :param get_children: 获取当前节点所有子节点的函数，返回子节点列表
    """
    should_continue = process_node_func(root)

    if not should_continue:
        return

    for child in get_children(root):
        PreorderAST(child, process_node_func, get_children)


# %% is_array
@overload
def is_array(node: Type) -> bool: ...
@overload
def is_array(node: Cursor) -> bool: ...
def is_array(x) -> bool:
    def _is_array_kind(kind: TypeKind) -> bool:
        """判断 TypeKind 是否属于数组类型"""
        return kind in (
            TypeKind.CONSTANTARRAY,
            TypeKind.INCOMPLETEARRAY,
            TypeKind.VARIABLEARRAY,
        )

    if isinstance(x, Type):
        return _is_array_kind(x.get_canonical().kind)
    elif isinstance(x, Cursor):
        x_type: Type = x.type
        if x_type:
            return _is_array_kind(x_type.get_canonical().kind)
    return False


# %% is_pointer
@overload
def is_pointer(node: Type) -> bool: ...
@overload
def is_pointer(node: Cursor) -> bool: ...
def is_pointer(x) -> bool:
    def _is_pointer_kind(kind: TypeKind) -> bool:
        """判断 TypeKind 是否属于指针类型"""
        return kind == TypeKind.POINTER

    if isinstance(x, Type):
        return _is_pointer_kind(x.get_canonical().kind)
    elif isinstance(x, Cursor):
        x_type: Type = x.type
        if x_type:
            return _is_pointer_kind(x_type.get_canonical().kind)
    return False


# %% is_func_pointer
@overload
def is_func_pointer(node: Type) -> bool: ...
@overload
def is_func_pointer(node: Cursor) -> bool: ...
def is_func_pointer(x) -> bool:
    def _is_func_pointer(t: Type) -> bool:
        canonical = t.get_canonical()
        # 函数指针的类型是 "pointer to function"
        pointee = canonical.get_pointee()
        if pointee:
            return pointee.kind == TypeKind.FUNCTIONPROTO
        return False

    if isinstance(x, Type):
        return _is_func_pointer(x)
    elif isinstance(x, Cursor):
        x_type: Type = x.type
        if x_type:
            return _is_func_pointer(x_type)
    return False


# %% is_static
@overload
def is_static(x: Type) -> bool: ...
@overload
def is_static(x: Cursor) -> bool: ...
def is_static(x) -> bool:
    """
    判断是否为 static 声明
    """
    if isinstance(x, Cursor):
        # 检查存储类是否为 static
        if x.storage_class == StorageClass.STATIC:
            return True
        # C++ static 方法
        if x.is_static_method():
            return True
    return False


# %% is_union
@overload
def is_union(x: Type) -> bool: ...
@overload
def is_union(x: Cursor) -> bool: ...
def is_union(x) -> bool:
    """
    判断是否为 union 联合体类型。
    """
    if isinstance(x, Type):
        if x.kind == TypeKind.UNION:
            return True
        elif x.kind == TypeKind.RECORD:
            decl = x.get_declaration()
            return decl.kind == CursorKind.UNION_DECL
        elif x.kind == TypeKind.ELABORATED:
            pointee = x.get_pointee()
            if pointee:
                return is_union(pointee)
        return False

    elif isinstance(x, Cursor):
        return is_union(x.type)

    return False


# %% is_struct
@overload
def is_struct(x: Type) -> bool: ...
@overload
def is_struct(x: Cursor) -> bool: ...
def is_struct(x) -> bool:
    """
    判断是否为 struct 类型。
    """
    if isinstance(x, Type):
        if x.kind == TypeKind.RECORD:
            decl = x.get_declaration()
            return decl.kind == CursorKind.STRUCT_DECL
        elif x.kind == TypeKind.ELABORATED:
            # 处理如 "struct S" 这种带标签的类型
            # TODO 这里不是褪去指针, 因为这个分支不是TypeKind.POINTER
            pointee = x.get_pointee()
            if pointee:
                return is_struct(pointee)
        return False

    elif isinstance(x, Cursor):
        return is_struct(x.type)

    return False

# %% type_name
@overload
def typeName(x: Type): ...
@overload
def typeName(x: Cursor): ...
def typeName(x) -> str | None:
    if isinstance(x, Type):
        return x.spelling.strip()
    elif isinstance(x, Cursor):
        if x.type:
            return x.type.spelling.strip()
    else:
        return None


# %% pure_type_name
@overload
def pureTypeName(x: Type): ...
@overload
def pureTypeName(x: Cursor): ...
def pureTypeName(x) -> str | None:

    def get_pure_name(t: Type) -> str:
        # 获取规范类型（消除 const/volatile/typedef）
        t_type: Type = t.get_canonical()
        while True:
            if is_array(t_type):
                t_type = t_type.element_type
            elif is_pointer(t_type):
                t_type = t_type.get_pointee()
            else:
                break
        return t_type.spelling.strip()

    # 分发逻辑
    if isinstance(x, Type):
        return get_pure_name(x)
    elif isinstance(x, Cursor):
        if x.type:
            return get_pure_name(x.type)
    else:
        return None


def nodeName(x: Cursor | Type) -> str | None:
    """
    对于 Cursor 或 Type 返回它的类型
    """
    return x.spelling.strip()


def is_globalVarDeclNode(node: Cursor) -> bool:
    """
    判断当前节点是否是一个全局变量的声明
    """
    if node.kind != CursorKind.VAR_DECL:
        return False

    parent = node.semantic_parent
    if parent is None:
        return False

    return parent.kind == CursorKind.TRANSLATION_UNIT


def is_funcDecl(node: Cursor) -> bool:
    return node.kind == CursorKind.FUNCTION_DECL


def is_funcDef(node: Cursor) -> bool:
    return node.kind == CursorKind.FUNCTION_DECL and node.is_definition()




def is_callVarNode(node: Cursor) -> bool:
    raise NotImplementedError  # TODO 这个还要更进一步检查
    return node.kind == CursorKind.DECL_REF_EXPR


# %% AI generate, not Review
def is_callLocalVarNode(node: Cursor) -> bool:
    """
    判断一个 Cursor 是否表示对局部变量的访问。

    - 只有表达式节点才可能是变量访问
    - 必须是标识符引用（DeclRefExpr）
    - 引用的声明必须是局部变量（非全局、非参数、非函数等）
    """
    # 检查是否为声明引用表达式（即变量被使用的地方）
    if not is_callVarNode(node):
        return False

    # 获取引用的声明
    referenced_decl = node.referenced
    if referenced_decl is None:
        return False

    # 判断声明的种类是否为局部变量
    # 局部变量通常是 VarDecl，且在其父作用域中是函数体内的
    if referenced_decl.kind == CursorKind.VAR_DECL:
        # 检查其父节点是否为函数体（即不是文件作用域的全局变量）
        parent = referenced_decl.semantic_parent
        if parent is not None and parent.kind == CursorKind.FUNCTION_DECL:
            return True

    return False


def is_callGlobalVarNode(node: Cursor) -> bool:
    """
    判断一个 Cursor 是否表示对全局变量的访问。

    条件：
    - 节点是声明引用表达式 (DECL_REF_EXPR)
    - 引用的声明是一个 VAR_DECL
    - 该变量的声明位于文件作用域（即其父节点是翻译单元或非函数内）
    """
    # 必须是变量引用表达式
    if not is_callVarNode(node):
        return False

    referenced_decl = node.referenced
    if referenced_decl is None:
        return False

    # 必须是变量声明
    if referenced_decl.kind != CursorKind.VAR_DECL:
        return False

    # 检查其父作用域：如果是翻译单元（TranslationUnit），说明是全局变量
    parent = referenced_decl.semantic_parent
    if parent is not None and parent.kind == CursorKind.TRANSLATION_UNIT:
        return True

    return False


def is_callFuncParam(node: Cursor) -> bool:
    """
    检查是否为 函数形参的调用
    """
    raise NotImplementedError
    pass


def is_localVarDeclNode(node: Cursor) -> bool:
    """
    判断一个 Cursor 是否表示一个局部变量的声明
    """
    # 必须是变量声明
    if node.kind != CursorKind.VAR_DECL:
        return False

    # 获取语义上的父节点（即声明所在的作用域）
    parent = node.semantic_parent
    if parent is None:
        return False

    # 如果父节点是函数，则是局部变量
    return parent.kind == CursorKind.FUNCTION_DECL


def is_varAssignNode(node):
    """
    判断当前节点是否是一个变量赋值表达式, 不处理+=运算

    条件：
    - 节点是一个二元运算表达式（Binary Operator）
    - 运算符是赋值操作（=）
    - 左操作数是一个变量引用（即被赋值的是变量）
    """
    # 必须是二元运算表达式
    if node.kind != CursorKind.BINARY_OPERATOR:
        return False

    # 获取运算符符号
    op = node.displayname.strip()
    if op != "=":
        return False  # 只匹配简单赋值，不包括 +=, -= 等

    # 检查左操作数是否是变量引用（即 DeclRefExpr）
    children = list(node.get_children())
    if not children:
        return False

    left_child = children[0]  # 左操作数在二元操作符中是第一个子节点
    if left_child.kind == CursorKind.DECL_REF_EXPR:
        # 可选：进一步确认引用的是变量（VAR_DECL 或 PARM_DECL）
        referenced = left_child.referenced
        if referenced and referenced.kind in (
            CursorKind.VAR_DECL,
            CursorKind.PARM_DECL,
        ):
            return True

    return False


def is_callFuncNode(node: Cursor) -> bool:
    """
    判断当前节点是否是一个函数调用表达式。

    条件：
    - 节点是一个调用表达式 (CALL_EXPR)
    - 子节点中包含被调用的函数声明引用（通常是 DECL_REF_EXPR 或 MEMBER_REF）
    """
    # 检查节点是否为调用表达式
    if node.kind != CursorKind.CALL_EXPR:
        return False

    # 可选：确保调用的是一个函数或函数指针
    # 调用表达式的第一个子节点通常是被调用的函数
    children = list(node.get_children())
    if not children:
        return False

    callee = children[0]  # 调用目标是第一个子节点

    # 常见情况：函数名引用
    if callee.kind == CursorKind.DECL_REF_EXPR:
        referenced = callee.referenced
        if referenced and referenced.kind in (
            CursorKind.FUNCTION_DECL,
            CursorKind.CXX_METHOD,
        ):
            return True

    # 支持函数指针调用或成员函数调用等
    elif callee.kind in (
        CursorKind.MEMBER_REF,
        CursorKind.MEMBER_REF_EXPR,
        CursorKind.PAREN_EXPR,
        CursorKind.UNARY_OPERATOR,
    ):
        # 这些可能是函数指针或复杂调用，也可以视为函数调用
        return True

    return False


def is_arrIdxNode(node: Cursor) -> bool:
    """
    判断当前节点是否是一个数组下标访问表达式（即形如 arr[idx]）。

    条件：
    - 节点是数组下标表达式（ARRAY_SUBSCRIPT_EXPR）
    - 通常有两个子节点：数组名 和 索引
    """
    # 检查节点是否为数组下标表达式
    if node.kind != CursorKind.ARRAY_SUBSCRIPT_EXPR:
        return False

    # 可选：进一步检查是否有两个子节点（数组基地址和索引）
    children = list(node.get_children())
    if len(children) < 2:
        return False

    # 常见结构：第一个子节点是数组（如 DeclRefExpr），第二个是索引表达式
    array_child = children[0]
    index_child = children[1]

    # 可以进一步验证语义合理性（非必须）
    # 例如 array_child 是变量引用，index_child 是整型表达式等

    return True


def is_forLoopNode(node: Cursor) -> bool:
    return node.kind == CursorKind.FOR_STMT


def is_whileLoopNode(node: Cursor) -> bool:
    return node.kind == CursorKind.WHILE_STMT


def is_parameterRefNode(node: Cursor) -> bool:
    """
    检查一个节点是否为对函数形参的引用（即在函数体中使用了参数变量）。
    """
    if node.kind == CursorKind.DECL_REF_EXPR:
        referenced_decl = node.referenced
        if referenced_decl and referenced_decl.kind == CursorKind.PARM_DECL:
            return True
    return False


def is_returnStatement(node: Cursor) -> bool:
    """
    判断给定的 Cursor 是否为 return 语句
    """
    return node.kind == CursorKind.RETURN_STMT


def is_switchStmt(node: Cursor) -> bool:
    return node.kind == CursorKind.SWITCH_STMT


def is_ifStmt(node: Cursor) -> bool:
    return node.kind == CursorKind.IF_STMT


def getVarInExpr(node: Cursor):
    """
    从一个表达式中获取所有的变量, 形如 x+y 中提取 x, y
    """
    pass
