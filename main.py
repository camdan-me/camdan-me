import re
import os
import sys
import ast
from enum import Enum
from datetime import datetime

import chess
import chess.pgn
import yaml
from github import Github

import src.markdown as markdown

class Action(Enum):
    UNKNOWN = 0
    MOVE = 1
    NEW_GAME = 2


def update_top_moves(user):
    with open('data/top_moves.txt', 'r') as file:
        contents = file.read()
        dictionary = ast.literal_eval(contents)

    if user not in dictionary:
        dictionary[user] = 1
    else:
        dictionary[user] += 1

    with open('data/top_moves.txt', 'w') as file:
        file.write(str(dictionary))


def update_last_moves(line):
    with open('data/last_moves.txt', 'r+') as last_moves:
        content = last_moves.read()
        last_moves.seek(0, 0)
        last_moves.write(line.rstrip('\r\n') + '\n' + content)


def replace_text_between(original_text, marker, replacement_text):
    delimiter_a = marker['begin']
    delimiter_b = marker['end']

    if original_text.find(delimiter_a) == -1 or original_text.find(delimiter_b) == -1:
        return original_text

    leading_text = original_text.split(delimiter_a)[0]
    trailing_text = original_text.split(delimiter_b)[1]

    return leading_text + delimiter_a + replacement_text + delimiter_b + trailing_text


def parse_issue(title):
    if title.lower() == 'chess: start new game':
        return (Action.NEW_GAME, None)

    if 'chess: move' in title.lower():
        match_obj = re.match('Chess: Move ([A-H][1-8]) to ([A-H][1-8])', title, re.I)

        source = match_obj.group(1)
        dest   = match_obj.group(2)
        return (Action.MOVE, (source + dest).lower())

    return (Action.UNKNOWN, None)


def main(issue, issue_author, repo_owner):
    action = parse_issue(issue.title)
    gameboard = chess.Board()

    with open('data/settings.yaml', 'r') as settings_file:
        settings = yaml.load(settings_file, Loader=yaml.FullLoader)

    if action[0] == Action.NEW_GAME:
        if os.path.exists('games/current.pgn') and issue_author != repo_owner:
            issue.create_comment(settings['comments']['invalid_new_game'].format(author=issue_author))
            issue.edit(state='closed')
            return False, 'ERROR: A current game is in progress. Only the repo owner can start a new game'

        issue.create_comment(settings['comments']['successful_new_game'].format(author=issue_author))
        issue.edit(state='closed')

        with open('data/last_moves.txt', 'w') as last_moves:
            last_moves.write('Start game: ' + issue_author)

        game = chess.pgn.Game()
        game.headers['Event'] = repo_owner + '\'s Online Open Chess Tournament'
        game.headers['Site'] = 'https://github.com/' + os.environ['GITHUB_REPOSITORY']
        game.headers['Date'] = datetime.now().strftime('%Y.%m.%d')
        game.headers['Round'] = '1'

    elif action[0] == Action.MOVE:
        if not os.path.exists('games/current.pgn'):
            return False, 'ERROR: There is no game in progress! Start a new game first'

        with open('games/current.pgn') as pgn_file:
            game = chess.pgn.read_game(pgn_file)
            gameboard = game.board()

        with open('data/last_moves.txt') as moves:
            line = moves.readline()
            last_player = line.split(':')[1].strip()
            last_move   = line.split(':')[0].strip()

        for move in game.mainline_moves():
            gameboard.push(move)

        if action[1][:2] == action[1][2:]:
            issue.create_comment(settings['comments']['invalid_move'].format(author=issue_author, move=action[1]))
            issue.edit(state='closed', labels=['❌ Invalid', '❗ Bad Move'])
            return False, 'ERROR: Move is invalid!'

        if chess.Move.from_uci(action[1] + 'q') in gameboard.legal_moves:
            action = (action[0], action[1] + 'q')

        move = chess.Move.from_uci(action[1])

        if last_player == issue_author and 'Start game' not in last_move:
            issue.create_comment(settings['comments']['consecutive_moves'].format(author=issue_author))
            issue.edit(state='closed', labels=['❌ Invalid', '❗ Double Move'])
            return False, 'ERROR: Two moves in a row!'

        if move not in gameboard.legal_moves:
            issue.create_comment(settings['comments']['invalid_move'].format(author=issue_author, move=action[1]))
            issue.edit(state='closed', labels=['❌ Invalid', '❗ Bad Move'])
            return False, 'ERROR: Move is invalid!'

        if not gameboard.is_valid():
            issue.create_comment(settings['comments']['invalid_board'].format(author=issue_author))
            issue.edit(state='closed', labels=['❌ Invalid', '❗ Fatal Error'])
            return False, 'ERROR: Board is invalid!'

        issue_labels = ['⚔️ Capture!'] if gameboard.is_capture(move) else []
        issue_labels += ['Blue' if gameboard.turn == chess.WHITE else 'Red']

        issue.create_comment(settings['comments']['successful_move'].format(author=issue_author, move=action[1]))
        issue.edit(state='closed', labels=issue_labels)

        update_last_moves(action[1] + ': ' + issue_author)
        update_top_moves(issue_author)

        gameboard.push(move)
        game.end().add_main_variation(move, comment=issue_author)
        game.headers['Result'] = gameboard.result()

    elif action[0] == Action.UNKNOWN:
        issue.create_comment(settings['comments']['unknown_command'].format(author=issue_author))
        issue.edit(state='closed', labels=['❌ Invalid', '❗ Bad Command'])
        return False, 'ERROR: Unknown action'

    print(game, file=open('games/current.pgn', 'w'), end='\n\n')

    last_moves = markdown.generate_last_moves()

    if gameboard.is_game_over():
        win_msg = {
            '1/2-1/2': 'It\'s a draw',
            '1-0': 'Blue wins',
            '0-1': 'Red wins'
        }

        with open('data/last_moves.txt', 'r') as last_moves_file:
            lines = last_moves_file.readlines()
            pattern = re.compile('.*: (@[a-z\\d](?:[a-z\\d]|-(?=[a-z\\d])){0,38})', flags=re.I)
            player_list = { re.match(pattern, line).group(1) for line in lines }

        if gameboard.result() == '1/2-1/2':
            issue.add_to_labels('👑 Draw!')
        else:
            issue.add_to_labels('👑 Winner!')

        issue.create_comment(settings['comments']['game_over'].format(
            outcome=win_msg.get(gameboard.result(), 'UNKNOWN'),
            players=', '.join(player_list),
            num_moves=len(lines)-1,
            num_players=len(player_list)))

        os.rename('games/current.pgn', datetime.now().strftime('games/game-%Y%m%d-%H%M%S.pgn'))
        os.remove('data/last_moves.txt')

    with open('README.md', 'r') as file:
        readme = file.read()
        readme = replace_text_between(readme, settings['markers']['board'], '{chess_board}')
        readme = replace_text_between(readme, settings['markers']['moves'], '{moves_list}')
        readme = replace_text_between(readme, settings['markers']['turn'], '{turn}')
        readme = replace_text_between(readme, settings['markers']['last_moves'], '{last_moves}')
        readme = replace_text_between(readme, settings['markers']['top_moves'], '{top_moves}')

    with open('README.md', 'w') as file:
        file.write(readme.format(
            chess_board=markdown.board_to_markdown(gameboard),
            moves_list=markdown.generate_moves_list(gameboard),
            turn=('blue' if gameboard.turn == chess.WHITE else 'red'),
            last_moves=last_moves,
            top_moves=markdown.generate_top_moves()))

    return True, ''


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--self-test':
        sys.exit(0)
    else:
        repo = Github(os.environ['GITHUB_TOKEN']).get_repo(os.environ['GITHUB_REPOSITORY'])
        issue = repo.get_issue(number=int(os.environ['ISSUE_NUMBER']))
        issue_author = '@' + issue.user.login
        repo_owner = '@' + os.environ['REPOSITORY_OWNER']

    ret, reason = main(issue, issue_author, repo_owner)

    if ret == False:
        sys.exit(reason)
