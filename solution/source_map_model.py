from __future__ import annotations

BASE64 = {ch: i for i, ch in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/')}


def decode_vlq(segment):
    values = []
    value = shift = 0
    for ch in segment:
        digit = BASE64[ch]
        value |= (digit & 31) << shift
        if digit & 32:
            shift += 5
            continue
        negative = value & 1
        decoded = value >> 1
        values.append(-decoded if negative else decoded)
        value = shift = 0
    if shift:
        raise ValueError('unterminated source-map VLQ segment')
    return values


def decode_basic_map(source_map):
    decoded = []
    source = original_line = original_column = name = 0
    lines = source_map.get('mappings', '').split(';')
    for generated_line, line in enumerate(lines):
        generated_column = 0
        line_segments = []
        if line:
            for raw in line.split(','):
                fields = decode_vlq(raw)
                generated_column += fields[0]
                if len(fields) == 1:
                    line_segments.append({'generated_column': generated_column, 'mapped': False})
                    continue
                if len(fields) not in (4, 5):
                    raise ValueError('unsupported source-map segment width')
                source += fields[1]
                original_line += fields[2]
                original_column += fields[3]
                item = {
                    'generated_column': generated_column,
                    'mapped': True,
                    'source': source_map['sources'][source],
                    'original_line': original_line,
                    'original_column': original_column,
                    'name': None,
                }
                if len(fields) == 5:
                    name += fields[4]
                    item['name'] = source_map['names'][name]
                line_segments.append(item)
        decoded.append(line_segments)
    return decoded


def resolve_basic(source_map, line, column):
    decoded = source_map.setdefault('_decoded_cache', decode_basic_map(source_map))
    if line < 0 or line >= len(decoded):
        return None
    candidate = None
    for segment in decoded[line]:
        if segment['generated_column'] > column:
            break
        candidate = segment
    if not candidate or not candidate['mapped']:
        return None
    return {key: candidate[key] for key in ('source', 'original_line', 'original_column', 'name')}


def resolve_indexed(index_map, line, column):
    chosen = None
    for section in index_map['sections']:
        off = section['offset']
        if (off['line'], off['column']) <= (line, column):
            chosen = section
        else:
            break
    if chosen is None:
        return None
    off = chosen['offset']
    local_line = line - off['line']
    local_column = column - off['column'] if local_line == 0 else column
    if local_column < 0:
        return None
    return resolve_basic(chosen['map'], local_line, local_column)


def resolve_two_stage(index_map, chunk_maps, line, column):
    first = resolve_indexed(index_map, line, column)
    if first is None:
        return None
    chunk = first['source']
    second_map = chunk_maps.get(chunk)
    if second_map is None:
        return None
    second = resolve_basic(second_map, first['original_line'], first['original_column'])
    if second is None:
        return None
    return {
        'chunk': chunk,
        'source': second['source'],
        'name': second['name'],
        'original_line': second['original_line'],
        'original_column': second['original_column'],
    }
